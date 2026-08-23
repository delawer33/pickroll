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
    def test_первый_сезон_лиги_входит(self) -> None:
        assert rows.is_historical_era("1946-47")

    def test_сезон_до_основания_лиги_не_входит(self) -> None:
        assert not rows.is_historical_era("1945-46")

    def test_поздний_сезон_входит(self) -> None:
        assert rows.is_historical_era("2024-25")


class TestMetricTracked:
    def test_метрика_не_считается_до_сезона_введения(self) -> None:
        assert not rows.metric_tracked("blocks", "1961-62")
        assert not rows.metric_tracked("blocks", "1972-73")

    def test_метрика_считается_с_сезона_введения(self) -> None:
        assert rows.metric_tracked("blocks", "1973-74")
        assert rows.metric_tracked("three_pointers_made", "1979-80")

    def test_неизвестная_метрика_считается_всегда(self) -> None:
        assert rows.metric_tracked("points", "1946-47")


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

    def test_матч_до_основания_лиги_отбрасывается(self) -> None:
        # Идентификатор непривычной формы — сезон берётся из даты: январь 1946 это 1945-46.
        row = self._row(gameId="146", gameDate="1946-01-15 19:30:00")

        assert rows.box_score_row(row) is None

    def test_невведённые_метрики_старой_эры_читаются_как_none(self) -> None:
        # Датасет заполняет невведённые метрики нулями: в 1961-62 блоки и перехваты
        # никто не считал, и ноль там — ложь. Решает METRIC_INTRODUCED, а не значение.
        row = self._row(
            gameId="26100001",
            gameDate="1962-03-02 19:30:00",
            steals="0.0",
            blocks="0.0",
            turnovers="0.0",
            reboundsOffensive="0.0",
            threePointersMade="0.0",
            threePointersAttempted="0.0",
        )

        parsed = rows.box_score_row(row)

        assert parsed is not None
        assert parsed[2] == "1961-62"
        assert parsed[17] is None  # blocks
        assert parsed[16] is None  # steals
        assert parsed[18] is None  # turnovers
        assert parsed[14] is None  # rebounds_offensive
        assert parsed[22] is None  # three_pointers_made
        # Очки считались с основания лиги — они остаются числом.
        assert parsed[11] == 26

    def test_современный_сезон_невведённых_метрик_не_имеет(self) -> None:
        parsed = rows.box_score_row(self._row(blocks="2", steals=""))

        assert parsed is not None
        assert parsed[17] == 2
        # Пустое значение введённой метрики — по-прежнему ноль, а не «неизвестно».
        assert parsed[16] == 0

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
