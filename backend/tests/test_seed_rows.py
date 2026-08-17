"""Юнит-тесты разбора строк датасета — чистая логика без БД."""

from datetime import date
from decimal import Decimal

from court_vision.seed import rows


class TestSeasonFromDate:
    def test_осенний_матч_относится_к_сезону_текущего_года(self) -> None:
        assert rows.season_from_date(date(2012, 11, 2)) == "2012-13"

    def test_весенний_матч_относится_к_сезону_предыдущего_года(self) -> None:
        assert rows.season_from_date(date(2013, 6, 20)) == "2012-13"

    def test_сезон_на_переломе_века(self) -> None:
        assert rows.season_from_date(date(1999, 11, 2)) == "1999-00"


class TestHistoricalEra:
    def test_сезон_с_начала_эры_входит(self) -> None:
        assert rows.is_historical_era("2005-06")

    def test_более_ранний_сезон_не_входит(self) -> None:
        assert not rows.is_historical_era("2004-05")

    def test_поздний_сезон_входит(self) -> None:
        assert rows.is_historical_era("2024-25")


class TestPlayerRow:
    def test_собирает_полное_имя_и_позицию(self) -> None:
        parsed = rows.player_row(
            {
                "personId": "2544",
                "firstName": "LeBron",
                "lastName": "James",
                "birthDate": "1984-12-30",
                "country": "USA",
                "heightInches": "81",
                "bodyWeightLbs": "250",
                "guard": "0",
                "forward": "1",
                "center": "0",
                "draftYear": "2003",
            }
        )

        assert parsed is not None
        assert parsed[3] == "LeBron James"
        assert parsed[8] == "F"

    def test_строка_без_идентификатора_отбрасывается(self) -> None:
        assert rows.player_row({"personId": "", "firstName": "X", "lastName": "Y"}) is None


class TestBoxScoreRow:
    def _row(self, **overrides: str) -> dict[str, str]:
        base = {
            "gameId": "21200001",
            "personId": "2544",
            "gameDate": "2012-11-02 19:30:00",
            "playerteamId": "1610612748",
            "opponentteamId": "1610612752",
            "home": "1",
            "win": "1",
            "startingPosition": "F",
            "numMinutes": "39.166666666666664",
            "points": "26",
            "assists": "7",
            "reboundsTotal": "8",
            "plusMinusPoints": "12",
        }
        return base | overrides

    def test_разбирает_строку_игрок_матч(self) -> None:
        parsed = rows.box_score_row(self._row())

        assert parsed is not None
        assert parsed[0] == 21200001
        assert parsed[1] == 2544
        assert parsed[2] == "2012-13"
        assert parsed[3] == date(2012, 11, 2)
        assert parsed[9] == Decimal("39.167")
        assert parsed[10] == 26

    def test_матч_до_исторической_эры_отбрасывается(self) -> None:
        assert rows.box_score_row(self._row(gameDate="2001-01-15 19:30:00")) is None

    def test_пустые_счётные_показатели_читаются_как_ноль(self) -> None:
        parsed = rows.box_score_row(self._row(points="", assists="", numMinutes=""))

        assert parsed is not None
        assert parsed[9] is None
        assert parsed[10] == 0
        assert parsed[11] == 0
