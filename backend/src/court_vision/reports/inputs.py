"""Вход генерации отчёта: всё, что модель вправе знать об игроке в сезоне.

Вход собирается из тех же сервисов, что и Инструменты агента, — сезонная статистика,
Перцентили, Похожие игроки, — и это единственный источник фактов отчёта. Проверка готового
отчёта (`generate.check_grounding`) сверяет ссылки модели именно с ним: что не попало во вход,
в отчёте появиться не может.

Сервис не коммитит: сборка входа только читает.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from court_vision.players.errors import FingerprintMissing, SeasonDataMissing
from court_vision.players.percentiles import player_percentiles
from court_vision.players.schemas import (
    FeatureZ,
    PlayerIdentity,
    PlayerPercentilesResponse,
    SeasonStats,
    SimilarPlayersResponse,
)
from court_vision.players.service import player_identity, resolve_player, season_stats
from court_vision.players.similar import similar_players_for
from court_vision.reports.schemas import ReportLanguage

# Сколько Похожих игроков кладётся во вход: столько, чтобы выбрать из них 2–3 сопоставимых,
# и не больше — каждый несёт объяснение сходства, то есть токены.
SIMILAR_LIMIT: Final = 5


@dataclass(frozen=True, slots=True)
class ReportInput:
    player: PlayerIdentity
    season: str
    language: ReportLanguage
    stats: SeasonStats
    percentiles: PlayerPercentilesResponse
    # None — у сезона нет Статистического отпечатка (ранняя эра, мало игр): сопоставимых
    # игроков в отчёте быть не может, и модель получает это явно, а не пустым списком.
    similar: SimilarPlayersResponse | None

    def metric_keys(self) -> list[str]:
        """Ключи метрик, на которые отчёт вправе ссылаться: те, что во входе имеют значение.

        Метрика, не считавшаяся в ту эпоху (`value: null`), в список не попадает —
        утверждение про неё было бы утверждением про данные, которых нет.
        """
        keys = [metric.metric for metric in self.percentiles.metrics if metric.value is not None]
        if self.similar is not None:
            keys.extend(item.metric for item in self.similar.target_profile)
        return list(dict.fromkeys(keys))

    def comparable_keys(self) -> set[tuple[str, str]]:
        """Пары (имя, сезон) Похожих игроков входа — единственные допустимые сопоставимые."""
        if self.similar is None:
            return set()
        return {
            (item.player.full_name.casefold(), item.season) for item in self.similar.similar_players
        }

    def prompt_payload(self) -> dict[str, Any]:
        identity = self.player.model_dump(mode="json", exclude={"player_id"}, exclude_none=True)
        fingerprint: dict[str, Any] | None = None
        if self.similar is not None:
            fingerprint = {
                "profile": [
                    {"key": item.metric, "label": item.label, "z": item.z}
                    for item in self.similar.target_profile
                ],
                "similar_players": [
                    {
                        "name": item.player.full_name,
                        "season": item.season,
                        "similarity": item.similarity,
                        "matching": _features(item.matching),
                        "diverging": _features(item.diverging),
                    }
                    for item in self.similar.similar_players
                ],
            }
        return {
            "language": self.language,
            "player": identity,
            "season": self.season,
            "games_played": self.percentiles.games_played,
            "qualified": self.percentiles.qualified,
            "qualified_sample_size": self.percentiles.sample_size,
            "averages": self.stats.averages.model_dump(mode="json"),
            "metrics": [
                {
                    "key": metric.metric,
                    "label": metric.label,
                    "value": metric.value,
                    "percentile": metric.percentile,
                    "lower_is_better": metric.lower_is_better,
                }
                for metric in self.percentiles.metrics
            ],
            "fingerprint": fingerprint,
            "allowed_metric_keys": self.metric_keys(),
        }

    def prompt(self) -> str:
        # ensure_ascii=False: экранированная кириллица раздувает промпт на ровном месте.
        return json.dumps(self.prompt_payload(), ensure_ascii=False, indent=1)


def _features(features: list[FeatureZ]) -> list[dict[str, Any]]:
    return [
        {"key": f.metric, "label": f.label, "target_z": f.target_z, "similar_z": f.similar_z}
        for f in features
    ]


async def build_report_input(
    session: AsyncSession, *, player_name: str, season: str | None, language: ReportLanguage
) -> ReportInput:
    """Имя → вход отчёта; неопознанный игрок и сезон без данных — доменные ошибки.

    Игрок резолвится один раз, дальше сервисы ходят по нему, а не по имени: повторная
    Резолюция полного имени могла бы упасть на тёзке уже после того, как первая выбрала.
    """
    player = await resolve_player(session, player_name)
    percentiles = await player_percentiles(session, player=player, season=season)
    seasons = await season_stats(session, player_id=player.id, season=percentiles.season)
    if not seasons:  # Перцентили без сезонной статистики — рассинхрон выборок, а не данные
        raise SeasonDataMissing(player.id, player.full_name, percentiles.season)
    try:
        similar: SimilarPlayersResponse | None = await similar_players_for(
            session, player=player, season=percentiles.season, limit=SIMILAR_LIMIT
        )
    except FingerprintMissing:
        similar = None
    return ReportInput(
        player=player_identity(player),
        season=percentiles.season,
        language=language,
        stats=seasons[0],
        percentiles=percentiles,
        similar=similar,
    )
