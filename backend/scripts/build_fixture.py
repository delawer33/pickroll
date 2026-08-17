"""Сборка тестовой фикстуры из настоящего датасета.

Фикстура — подмножество реальных строк того же формата: несколько игроков и сезонов,
включая один сезон до начала Исторической эры (проверка фильтра) и однофамильцев
(проверка неоднозначности резолюции имени).

Запускается вручную при смене версии датасета:

    uv run python scripts/build_fixture.py --dataset-dir <куда скачан датасет>

Результат коммитится в git: tests/fixtures/dataset/.
"""

import argparse
import csv
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final

BACKEND_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = BACKEND_ROOT / "tests" / "fixtures" / "dataset"

# Демо-игрок (LeBron James), карьерная разбивка (Stephen Curry) и однофамильцы для
# проверки неоднозначности: у James Harden «James» — имя, у LeBron James — фамилия.
PLAYER_IDS: Final = {
    2544,  # LeBron James
    201939,  # Stephen Curry
    201935,  # James Harden
    977,  # Kobe Bryant — застаёт сезоны до 2005-06
}

# 2004-05 лежит в фикстуре намеренно: он должен отсеиваться фильтром Исторической эры.
SEASONS: Final = {f"{year}-{(year + 1) % 100:02d}" for year in range(2004, 2014)}

# Дополнительные игроки в Players.csv — им незачем иметь box scores, они нужны
# резолверу имени как кандидаты.
EXTRA_NAME_PATTERNS: Final = ("James", "Curry", "Bryant")


def _player_id(row: dict[str, str]) -> int | None:
    value = row["personId"].strip()
    return int(value) if value else None


def season_of(raw_date: str) -> str | None:
    value = raw_date.strip()[:10]
    if len(value) != 10:
        return None
    year, month = int(value[:4]), int(value[5:7])
    start = year if month >= 8 else year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def build(dataset_dir: Path) -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    _copy_whole(dataset_dir / "TeamHistories.csv")
    game_ids = _write_box_scores(dataset_dir / "PlayerStatistics.csv")
    _write_games(dataset_dir / "Games.csv", game_ids)
    _write_players(dataset_dir / "Players.csv")


def _copy_whole(source: Path) -> None:
    target = FIXTURE_DIR / source.name
    target.write_bytes(source.read_bytes())
    print(f"{target.name}: {target.stat().st_size} байт")


def _write_box_scores(source: Path) -> set[str]:
    target = FIXTURE_DIR / source.name
    game_ids: set[str] = set()

    with (
        source.open(newline="", encoding="utf-8") as src,
        target.open("w", newline="", encoding="utf-8") as dst,
    ):
        reader = csv.DictReader(src)
        assert reader.fieldnames is not None
        writer = csv.DictWriter(dst, fieldnames=reader.fieldnames)
        writer.writeheader()

        for row in reader:
            if _player_id(row) not in PLAYER_IDS:
                continue
            if season_of(row["gameDate"] or row["gameDateTimeEst"]) not in SEASONS:
                continue
            writer.writerow(row)
            game_ids.add(row["gameId"])

    print(f"{target.name}: {target.stat().st_size} байт, {len(game_ids)} матчей")
    return game_ids


def _write_games(source: Path, game_ids: set[str]) -> None:
    target = FIXTURE_DIR / source.name
    _filter_rows(source, target, lambda row: row["gameId"] in game_ids)


def _write_players(source: Path) -> None:
    target = FIXTURE_DIR / source.name

    def keep(row: dict[str, str]) -> bool:
        if _player_id(row) in PLAYER_IDS:
            return True
        name = f"{row['firstName']} {row['lastName']}"
        return any(pattern in name for pattern in EXTRA_NAME_PATTERNS)

    _filter_rows(source, target, keep)


def _filter_rows(source: Path, target: Path, keep: Callable[[dict[str, str]], bool]) -> None:
    kept = 0
    with (
        source.open(newline="", encoding="utf-8") as src,
        target.open("w", newline="", encoding="utf-8") as dst,
    ):
        reader = csv.DictReader(src)
        assert reader.fieldnames is not None
        writer = csv.DictWriter(dst, fieldnames=reader.fieldnames)
        writer.writeheader()
        for row in reader:
            if keep(row):
                writer.writerow(row)
                kept += 1

    print(f"{target.name}: {target.stat().st_size} байт, {kept} строк")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    args = parser.parse_args()

    if not args.dataset_dir.is_dir():
        sys.exit(f"Нет директории {args.dataset_dir}")

    build(args.dataset_dir)


if __name__ == "__main__":
    main()
