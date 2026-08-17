"""Юнит-тесты разбора строк датасета — чистая логика без БД."""

from datetime import date
from decimal import Decimal

from court_vision.players.models import UNKNOWN_GAME_TYPE
from court_vision.seed import rows


class TestSeasonFromDate:
    def test_осенний_матч_относится_к_сезону_текущего_года(self) -> None:
        assert rows.season_from_date(date(2012, 11, 2)) == "2012-13"

    def test_весенний_матч_относится_к_сезону_предыдущего_года(self) -> None:
        assert rows.season_from_date(date(2013, 6, 20)) == "2012-13"

    def test_сезон_на_переломе_века(self) -> None:
        assert rows.season_from_date(date(1999, 11, 2)) == "1999-00"


class TestSeasonFromGameId:
    def test_регулярный_чемпионат(self) -> None:
        assert rows.season_from_game_id("21200001") == "2012-13"

    def test_первый_сезон_данных_в_двадцатом_веке(self) -> None:
        assert rows.season_from_game_id("24600001") == "1946-47"

    def test_идентификатор_непривычной_формы(self) -> None:
        assert rows.season_from_game_id("") is None
        assert rows.season_from_game_id("2120000") is None


class TestSeasonOf:
    def test_матч_пузыря_остаётся_в_своём_сезоне(self) -> None:
        # Финал 2019-20 игрался в октябре 2020: по дате он неотличим от начала 2020-21.
        row = {"gameId": "41900406", "gameDate": "2020-10-11 21:00:00"}

        assert rows.season_of(row) == "2019-20"
        assert rows.season_from_date(date(2020, 10, 11)) == "2020-21"

    def test_без_идентификатора_сезон_берётся_из_даты(self) -> None:
        assert rows.season_of({"gameId": "", "gameDate": "2012-11-02"}) == "2012-13"


class TestGameTypeOf:
    def test_тип_читается_из_идентификатора(self) -> None:
        assert rows.game_type_of({"gameId": "41900406", "gameType": "Playoffs"}) == "Playoffs"
        assert rows.game_type_of({"gameId": "12500007"}) == "Preseason"

    def test_разные_написания_кубка_нормализуются(self) -> None:
        assert rows.game_type_of({"gameId": "62300001", "gameType": "NBA Cup"}) == "NBA Cup Final"
        assert (
            rows.game_type_of({"gameId": "62500001", "gameType": "Emirates NBA Cup"})
            == "NBA Cup Final"
        )

    def test_без_идентификатора_берётся_колонка(self) -> None:
        assert rows.game_type_of({"gameId": "", "gameType": "Regular Season"}) == "Regular Season"
        assert rows.game_type_of({}) == UNKNOWN_GAME_TYPE


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
        assert parsed[4] == "Regular Season"
        assert parsed[10] == Decimal("39.167")
        assert parsed[11] == 26

    def test_матч_до_исторической_эры_отбрасывается(self) -> None:
        row = self._row(gameId="20100015", gameDate="2001-01-15 19:30:00")

        assert rows.box_score_row(row) is None

    def test_пустые_счётные_показатели_читаются_как_ноль(self) -> None:
        parsed = rows.box_score_row(self._row(points="", assists="", numMinutes=""))

        assert parsed is not None
        assert parsed[10] is None
        assert parsed[11] == 0
        assert parsed[12] == 0

    def test_минуты_в_формате_mm_ss(self) -> None:
        parsed = rows.box_score_row(self._row(numMinutes="39:20"))

        assert parsed is not None
        assert parsed[10] == Decimal("39.333")

    def test_нечисловые_минуты_не_доезжают_до_колонки(self) -> None:
        # NaN Decimal принимает молча, а в numeric-колонке он отравил бы avg() за сезон.
        for value in ("nan", "не число"):
            parsed = rows.box_score_row(self._row(numMinutes=value))

            assert parsed is not None
            assert parsed[10] is None

    def test_бесконечность_в_целочисленной_колонке_не_роняет_разбор(self) -> None:
        parsed = rows.box_score_row(self._row(plusMinusPoints="inf"))

        assert parsed is not None
        assert parsed[26] is None
