"""Источник CSV-файлов датасета: скачивание с Kaggle или локальная директория.

Версия датасета запинена константой: датасет обновляется еженощно, а seed должен
быть воспроизводим. Скачивание идёт публичным эндпоинтом Kaggle — учётные данные
не нужны, поэтому seed выполняется одинаково локально и в CI.
"""

import csv
import shutil
import urllib.request
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Final

import structlog

DATASET_REF: Final = "eoinamoore/historical-nba-data-and-player-box-scores"
DATASET_VERSION: Final = 515

TEAMS_FILE: Final = "TeamHistories.csv"
PLAYERS_FILE: Final = "Players.csv"
GAMES_FILE: Final = "Games.csv"
BOX_SCORES_FILE: Final = "PlayerStatistics.csv"

DATASET_FILES: Final = (TEAMS_FILE, PLAYERS_FILE, GAMES_FILE, BOX_SCORES_FILE)

_DOWNLOAD_URL: Final = "https://www.kaggle.com/api/v1/datasets/download/{ref}/{file}"
_DOWNLOAD_TIMEOUT_SECONDS: Final = 60

# Минимальный контракт с датасетом: без этих колонок разбор бессмыслен.
REQUIRED_COLUMNS: Final = {
    TEAMS_FILE: {"teamId", "teamCity", "teamName", "teamAbbrev", "seasonActiveTill"},
    PLAYERS_FILE: {"personId", "firstName", "lastName"},
    GAMES_FILE: {"gameId", "gameDate", "hometeamId", "awayteamId", "gameType"},
    BOX_SCORES_FILE: {
        "gameId",
        "personId",
        "gameDate",
        "playerteamId",
        "opponentteamId",
        "numMinutes",
        "points",
    },
}

log = structlog.get_logger(__name__)


class DatasetSource:
    """Директория с CSV-файлами датасета — скачанными или лежащими локально (фикстура)."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path(self, file_name: str) -> Path:
        return self.directory / file_name

    def missing_files(self) -> list[str]:
        return [name for name in DATASET_FILES if not self.path(name).exists()]


def local_source(directory: Path) -> DatasetSource:
    source = DatasetSource(directory)
    missing = source.missing_files()
    if missing:
        raise FileNotFoundError(f"В {directory} нет файлов датасета: {', '.join(missing)}")
    return source


def download_source(cache_dir: Path) -> DatasetSource:
    """Скачивает запиненную версию датасета в кеш; уже скачанные файлы не перекачивает."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    source = DatasetSource(cache_dir)

    for file_name in DATASET_FILES:
        target = source.path(file_name)
        if target.exists():
            log.info("dataset.file.cached", file=file_name, path=str(target))
            continue
        _download_file(file_name, target)

    return source


def _download_file(file_name: str, target: Path) -> None:
    url = (
        f"{_DOWNLOAD_URL.format(ref=DATASET_REF, file=file_name)}"
        f"?datasetVersionNumber={DATASET_VERSION}"
    )
    log.info("dataset.file.download.start", file=file_name, version=DATASET_VERSION)

    partial = target.with_suffix(target.suffix + ".part")
    with (
        urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response,
        partial.open("wb") as handle,
    ):
        shutil.copyfileobj(response, handle, length=1024 * 1024)
        expected = response.headers.get("Content-Length")

    written = partial.stat().st_size
    # Оборванное соединение не поднимает исключения: copyfileobj просто вернётся раньше.
    # Без этой проверки обрезанный файл встал бы в кеш навсегда и «успешно» грузился.
    if expected is not None and written != int(expected):
        partial.unlink(missing_ok=True)
        raise OSError(f"{file_name}: скачано {written} байт из {expected}, файл неполный")

    partial.replace(target)
    log.info("dataset.file.download.done", file=file_name, bytes=written)


def read_rows(path: Path) -> Iterator[dict[str, str]]:
    """Потоковое чтение CSV: файл box scores не помещается в память целиком.

    `restval` важен: у короткой строки DictReader подставил бы None, и разбор упал бы
    на `.strip()` вместо того, чтобы отбросить строку.
    """
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, restval="")
        _ensure_expected_columns(path, reader.fieldnames)
        yield from reader


def _ensure_expected_columns(path: Path, fieldnames: Sequence[str] | None) -> None:
    """Переименование колонки в новой версии датасета обязано падать шумно.

    Иначе каждая строка тихо не разберётся, и seed отчитается об успехе с пустой таблицей.
    """
    required = REQUIRED_COLUMNS.get(path.name)
    if required is None:
        return

    missing = sorted(required - set(fieldnames or ()))
    if missing:
        raise ValueError(f"{path.name}: в датасете нет колонок {', '.join(missing)}")
