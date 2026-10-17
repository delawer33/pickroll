"""Похожие игроки: ближайшие Статистические отпечатки (см. CONTEXT.md).

Поиск точный, а не по HNSW: нужны «ближайший сезон каждого игрока» и «без самого игрока», а
приближённый индекс с фильтром после обхода графа молча теряет часть выдачи. Строк в таблице
десятки тысяч, девять компонент — полный скан косинуса укладывается в миллисекунды. Индекс
создан по решению PRD и ждёт своего часа (docs/deferred.md).

Сервис не коммитит: unit of work равен вызову Инструмента.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from court_vision.players.errors import FingerprintMissing
from court_vision.players.fingerprints import FEATURE_LABELS, FEATURES
from court_vision.players.models import Player, PlayerFingerprint
from court_vision.players.schemas import (
    FeatureProfile,
    FeatureZ,
    SimilarPlayer,
    SimilarPlayersResponse,
)
from court_vision.players.service import player_identity, resolve_player
from court_vision.rounding import round_half_up

MAX_LIMIT: Final = 10
DEFAULT_LIMIT: Final = 5
# Сколько признаков называть в объяснении с каждой стороны: три — столько модель способна
# пересказать, не превращая ответ в таблицу.
_EXPLAINED_FEATURES: Final = 3


async def find_similar_players(
    session: AsyncSession,
    *,
    player_name: str,
    season: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> SimilarPlayersResponse:
    """Ближайшие по косинусу отпечатки; сам игрок исключён, у каждого игрока — его самый
    близкий сезон. Без сезона отправная точка — последний сезон игрока с отпечатком."""
    limit = max(1, min(limit, MAX_LIMIT))
    player = await resolve_player(session, player_name)
    target = await _target_fingerprint(session, player, season)

    distance = PlayerFingerprint.embedding.cosine_distance(target.embedding)
    ranked = (
        select(
            PlayerFingerprint.player_id,
            PlayerFingerprint.season,
            PlayerFingerprint.z_scores,
            distance.label("distance"),
            # Самый близкий сезон каждого игрока: остальные его сезоны не пускаем в выдачу,
            # иначе один игрок занял бы весь список своими соседними сезонами.
            func.row_number()
            .over(partition_by=PlayerFingerprint.player_id, order_by=distance)
            .label("rank"),
        )
        .where(PlayerFingerprint.player_id != player.id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(Player, ranked.c.season, ranked.c.z_scores, ranked.c.distance)
            .join(ranked, ranked.c.player_id == Player.id)
            .where(ranked.c.rank == 1)
            # player_id в конце — детерминированный порядок при равных расстояниях.
            .order_by(ranked.c.distance, ranked.c.player_id)
            .limit(limit)
        )
    ).all()

    return SimilarPlayersResponse(
        player=player_identity(player),
        season=target.season,
        target_profile=[
            FeatureProfile(metric=f.key, label=f.label, z=target.z_scores[f.key]) for f in FEATURES
        ],
        similar_players=[
            _similar_player(candidate, candidate_season, z_scores, distance_value, target.z_scores)
            for candidate, candidate_season, z_scores, distance_value in rows
        ],
    )


async def pair_similarity(
    session: AsyncSession,
    *,
    player_a: str,
    season_a: str,
    player_b: str,
    season_b: str,
) -> float:
    """Косинусная близость отпечатков двух конкретных игрок-сезонов — для золотых пар eval."""
    first = await _target_fingerprint(session, await resolve_player(session, player_a), season_a)
    second = await _target_fingerprint(session, await resolve_player(session, player_b), season_b)
    distance = await session.scalar(
        select(PlayerFingerprint.embedding.cosine_distance(second.embedding)).where(
            PlayerFingerprint.player_id == first.player_id,
            PlayerFingerprint.season == first.season,
        )
    )
    assert distance is not None  # first только что прочитан
    return _similarity(float(distance))


async def _target_fingerprint(
    session: AsyncSession, player: Player, season: str | None
) -> PlayerFingerprint:
    seasons = tuple(
        await session.scalars(
            select(PlayerFingerprint.season)
            .where(PlayerFingerprint.player_id == player.id)
            .order_by(PlayerFingerprint.season)
        )
    )
    chosen = season if season is not None else (seasons[-1] if seasons else None)
    fingerprint = (
        await session.get(PlayerFingerprint, (player.id, chosen)) if chosen is not None else None
    )
    if fingerprint is None:
        raise FingerprintMissing(player.id, player.full_name, season, seasons)
    return fingerprint


def _similar_player(
    candidate: Player,
    season: str,
    z_scores: dict[str, float],
    distance: float,
    target_z: dict[str, float],
) -> SimilarPlayer:
    matching, diverging = explain(target_z, z_scores)

    def feature(key: str) -> FeatureZ:
        return FeatureZ(
            metric=key,
            label=FEATURE_LABELS[key],
            target_z=target_z[key],
            similar_z=z_scores[key],
        )

    return SimilarPlayer(
        player=player_identity(candidate),
        season=season,
        similarity=_similarity(distance),
        matching=[feature(key) for key in matching],
        diverging=[feature(key) for key in diverging],
    )


def explain(target_z: dict[str, float], other_z: dict[str, float]) -> tuple[list[str], list[str]]:
    """Ключи признаков: где профили совпадают сильнее всего и где расходятся сильнее всего.

    Совпадение — это не просто малая разница z: два игрока «на уровне лиги» по блокам
    совпадают ничем. Поэтому оценка совпадения — меньшее из двух отклонений за вычетом
    разрыва между ними: высоко, когда оба заметно отклоняются от лиги и в одну сторону
    (оба +2 — лучшее совпадение, оба около нуля — нейтральное). Расхождение — наибольший
    разрыв z. Порядок детерминирован: при равенстве оценок решает порядок признаков.
    """
    gap = {f.key: abs(target_z[f.key] - other_z[f.key]) for f in FEATURES}
    shared = {
        f.key: min(abs(target_z[f.key]), abs(other_z[f.key])) - gap[f.key]
        if target_z[f.key] * other_z[f.key] > 0
        else -gap[f.key]
        for f in FEATURES
    }
    keys = [f.key for f in FEATURES]
    matching = sorted(keys, key=lambda key: -shared[key])[:_EXPLAINED_FEATURES]
    rest = [key for key in keys if key not in matching]
    diverging = sorted(rest, key=lambda key: -gap[key])[:_EXPLAINED_FEATURES]
    return matching, diverging


def _similarity(distance: float) -> float:
    return round_half_up(max(-1.0, min(1.0, 1.0 - distance)), 3)
