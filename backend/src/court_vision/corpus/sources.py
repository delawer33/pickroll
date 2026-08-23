"""Выгрузка источников корпуса: официальный rulebook NBA (PDF) и Wikipedia (REST API).

Источник отдаёт `RawDocument` с плоским текстом, в котором сохранены структурные
заголовки, — по ним режет `chunking.py`. Сеть живёт только здесь: тесты кормят
чанкинг и ingest готовыми `RawDocument`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import quote

import httpx
import structlog
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from court_vision.corpus.models import SourceType
from court_vision.players.models import Game, Player, Team

log = structlog.get_logger(__name__)

# Rulebook запинен по URL и sha256: NBA перезаливает файл под тем же адресом между
# сезонами, и без пина корпус «плыл» бы молча. Смена сезона — осознанная правка этих
# двух констант и переиндексация.
RULEBOOK_URL: Final = (
    "https://ak-static.cms.nba.com/wp-content/uploads/sites/4/2025/10/"
    "Official-2025-26-NBA-Playing-Rules.pdf"
)
RULEBOOK_SHA256: Final = "3def8612e88622e3bd6cd0e53801a5fb3a4543ccaaade0670216094e0e962a0c"
RULEBOOK_TITLE: Final = "NBA Official Playing Rules 2025-26"

_WIKIPEDIA_API: Final = "https://en.wikipedia.org/w/rest.php/v1/page/{title}"
_WIKIPEDIA_PAGE: Final = "https://en.wikipedia.org/wiki/{title}"

# CDN NBA отвечает 403 на «неузнаваемые» клиенты — представляемся браузером.
_USER_AGENT: Final = "Mozilla/5.0 (X11; Linux x86_64) court-vision/0.1"

_TIMEOUT_SECONDS: Final = 30.0


class SourceIntegrityError(Exception):
    """Скачанный файл не совпал с запиненным sha256 — источник подменили или перезалили."""


@dataclass(frozen=True)
class RawDocument:
    """Плоский текст документа с сохранёнными заголовками — вход чанкинга."""

    source_type: SourceType
    title: str
    url: str | None
    text: str


def fetch_rulebook(cache_dir: Path) -> RawDocument:
    """Скачивает (или берёт из кеша) запиненный PDF правил и извлекает текст.

    Кеш по имени файла: PDF в полтора мегабайта незачем тянуть на каждый прогон,
    а sha256 проверяется и у кешированного — битый недокачанный файл не пройдёт.
    """
    path = cache_dir / "nba-playing-rules-2025-26.pdf"
    if not path.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        response = httpx.get(
            RULEBOOK_URL,
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        response.raise_for_status()
        path.write_bytes(response.content)
        log.info("corpus.rulebook_downloaded", url=RULEBOOK_URL, bytes=len(response.content))

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != RULEBOOK_SHA256:
        raise SourceIntegrityError(
            f"rulebook sha256 mismatch: expected {RULEBOOK_SHA256}, got {digest} ({path})"
        )

    reader = PdfReader(path)
    text = "\n".join(page.extract_text() for page in reader.pages)
    return RawDocument(source_type="rulebook", title=RULEBOOK_TITLE, url=RULEBOOK_URL, text=text)


def iter_wikipedia(titles: Iterable[str]) -> Iterator[RawDocument]:
    """Статьи Wikipedia по списку заголовков — wikitext через REST API.

    404 — пропуск с логом, а не ошибка: список заголовков собирается эвристикой из БД,
    и часть игроков просто не имеет статьи (или называется иначе). Редиректы
    разыменовываются на один шаг — этого хватает на "LeBron James" → каноническое имя.
    """
    with httpx.Client(
        headers={"User-Agent": _USER_AGENT}, timeout=_TIMEOUT_SECONDS, follow_redirects=True
    ) as client:
        for title in titles:
            document = _fetch_article(client, title, redirects_left=1)
            if document is not None:
                yield document


def _fetch_article(client: httpx.Client, title: str, *, redirects_left: int) -> RawDocument | None:
    response = client.get(_WIKIPEDIA_API.format(title=quote(title.replace(" ", "_"), safe="")))
    if response.status_code == 404:
        log.info("corpus.wikipedia_missing", title=title)
        return None
    response.raise_for_status()
    payload = response.json()
    source = str(payload["source"])

    if source.lstrip().lower().startswith("#redirect"):
        target = _redirect_target(source)
        if target is None or redirects_left == 0:
            log.info("corpus.wikipedia_redirect_dropped", title=title)
            return None
        return _fetch_article(client, target, redirects_left=redirects_left - 1)

    canonical = str(payload.get("title") or title)
    key = str(payload.get("key") or canonical.replace(" ", "_"))
    return RawDocument(
        source_type="wikipedia",
        title=canonical,
        url=_WIKIPEDIA_PAGE.format(title=quote(key, safe="")),
        text=source,
    )


def _redirect_target(source: str) -> str | None:
    """Цель `#REDIRECT [[Target]]`; None, если разметка не разобралась."""
    opening = source.find("[[")
    closing = source.find("]]", opening)
    if opening == -1 or closing == -1:
        return None
    return source[opening + 2 : closing].split("|")[0].strip() or None


async def wikipedia_titles(session: AsyncSession) -> list[str]:
    """Заголовки для выгрузки: игроки и команды из БД плюс статьи сезонов NBA.

    Эвристика имён: полное имя игрока и «город + название» команды почти всегда и есть
    заголовок статьи; промахи закрывает обработка 404 в `iter_wikipedia`.
    """
    players = (await session.execute(select(Player.full_name).order_by(Player.id))).scalars()
    teams = (await session.execute(select(Team.city, Team.name).order_by(Team.id))).all()
    seasons_query = select(Game.season).distinct().order_by(Game.season)
    seasons = (await session.execute(seasons_query)).scalars()
    return [
        *players,
        *(f"{city} {name}" for city, name in teams),
        # "2012-13" → "2012–13 NBA season": в заголовках Wikipedia диапазон лет — через
        # en dash, не дефис.
        *(f"{season[:4]}–{season[5:]} NBA season" for season in seasons),
    ]
