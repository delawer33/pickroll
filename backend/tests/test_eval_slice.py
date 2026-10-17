"""Eval-срез: состав по конфигу и Золотому набору, сборка, загрузка в чистую базу.

Сборка и загрузка гоняются на настоящем Postgres: фикстура датасета + маленький корпус
(rulebook и две статьи Wikipedia, одна нужна набору, другая нет) собираются в срез, срез
заливается в свежесозданную базу в том же контейнере.
"""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from court_vision.answer_eval.__main__ import run as run_answer_eval
from court_vision.answer_eval.golden import GoldenAnswer, load_answers
from court_vision.config import Settings
from court_vision.corpus.models import Chunk, Document
from court_vision.eval_slice import derived, dump
from court_vision.eval_slice.config import (
    DEFAULT_CONFIG_PATH,
    SliceConfig,
    coverage_problems,
    load_config,
    seasons_in_questions,
    wanted_seasons,
    wikipedia_titles,
)
from court_vision.eval_slice.dump import BudgetExceededError, SliceBuildError, build_slice
from court_vision.eval_slice.load import (
    SliceLoadError,
    check_run_fits_slice,
    prepare_slice,
    slice_state,
)
from court_vision.eval_slice.manifest import MANIFEST_NAME, Manifest, read_manifest
from court_vision.seed.__main__ import asyncpg_dsn

EMBEDDER = "test/embedder"


def answer(identifier: str, question: str, **fields: Any) -> GoldenAnswer:
    return GoldenAnswer(
        id=identifier, category="stats", question=question, reference=("факт",), **fields
    )


def wiki_answer(identifier: str, question: str, *documents: str) -> GoldenAnswer:
    return answer(
        identifier,
        question,
        expect_tools=("search_corpus",),
        expect_sources=("wikipedia",),
        expect_documents=documents,
    )


class TestКонфиг:
    def test_сезоны_берутся_из_вопросов_а_не_из_эталона(self) -> None:
        got = answer("a", "Что было в 2012-13?")
        ref = GoldenAnswer(
            id="b", category="stats", question="Сколько блоков?", reference=("с 1973-74",)
        )

        assert seasons_in_questions([got, ref]) == {"2012-13"}

    def test_состав_сезонов_конфиг_последний_и_вопросы(self) -> None:
        config = SliceConfig(seasons=("1978-79",))
        questions = [answer("a", "Очки в 2012-13?"), answer("b", "А в 2035-36?")]

        seasons = wanted_seasons(
            config, questions, available=["1978-79", "2012-13", "2013-14", "2025-26"]
        )

        # 2035-36 нет в базе — в срез не просится; последний сезон базы берётся сам.
        assert seasons == ["1978-79", "2012-13", "2025-26"]

    def test_последний_сезон_отключается(self) -> None:
        config = SliceConfig(include_latest_season=False)

        assert wanted_seasons(config, [], available=["2012-13", "2025-26"]) == []

    def test_статьи_без_повторов_в_порядке_набора(self) -> None:
        questions = [
            wiki_answer("a", "q", "Kobe Bryant", "Stephen Curry"),
            wiki_answer("b", "q", "Stephen Curry", "Dell Curry"),
        ]

        assert wikipedia_titles(questions) == ["Kobe Bryant", "Stephen Curry", "Dell Curry"]

    def test_покрытие_ловит_пропущенный_сезон_и_статью(self) -> None:
        questions = [
            answer("season", "Очки в 2012-13?"),
            answer("future", "Очки в 2035-36?"),
            wiki_answer("doc", "q", "Kobe Bryant"),
        ]

        problems = coverage_problems(
            questions,
            slice_seasons=["2013-14"],
            source_seasons=["2012-13", "2013-14"],
            slice_documents=["NBA Rules"],
        )

        assert problems == [
            "season: сезона 2012-13 нет в срезе",
            "doc: статьи 'Kobe Bryant' нет в срезе",
        ]

    def test_вопрос_про_статью_без_названия_статьи_не_грузится(self) -> None:
        with pytest.raises(ValueError, match="expect_documents"):
            answer("a", "q", expect_tools=("search_corpus",), expect_sources=("wikipedia",))

    def test_боевой_конфиг_и_набор_согласованы(self) -> None:
        config = load_config(DEFAULT_CONFIG_PATH)
        answers = load_answers()

        # Каждый вопрос про сезон попадает в срез, если сезон вообще есть в базе.
        seasons = wanted_seasons(
            config,
            answers,
            available=[f"{year}-{(year + 1) % 100:02d}" for year in range(1946, 2026)],
        )
        assert "1978-79" in seasons
        assert "2025-26" in seasons
        assert set(seasons_in_questions(answers)) - {"2035-36"} <= set(seasons)
        assert wikipedia_titles(answers)


@pytest.fixture
async def corpus(clean_database: None, session_factory: async_sessionmaker[AsyncSession]) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        documents = [
            Document(
                source_type=kind,
                title=title,
                url="https://x",
                content_hash=f"hash-{title}",
                fetched_at=now,
            )
            for kind, title in [
                ("rulebook", "NBA Rules"),
                ("wikipedia", "LeBron James"),
                ("wikipedia", "Лишняя статья"),
            ]
        ]
        session.add_all(documents)
        await session.flush()
        for index, document in enumerate(documents):
            vector = [0.0] * 768
            vector[index] = 0.25
            session.add(
                Chunk(
                    document_id=document.id,
                    locator=f"Секция {index}",
                    text=f"Текст {document.title} про таймер атаки",
                    embedding=vector,
                    embedding_model=EMBEDDER,
                )
            )
        await session.commit()


@pytest.fixture
async def seasons(seeded_database: None, db_connection: asyncpg.Connection[Any]) -> list[str]:
    rows = await db_connection.fetch("SELECT DISTINCT season FROM games ORDER BY 1")
    found = [row["season"] for row in rows]
    assert len(found) >= 4
    return found


@pytest.fixture
def golden(seasons: list[str]) -> list[GoldenAnswer]:
    return [
        answer("stats", f"Очки Леброна в {seasons[1]}?"),
        wiki_answer("bio", "Кто такой Леброн?", "LeBron James"),
    ]


@pytest.fixture
async def built(
    corpus: None,
    seasons: list[str],
    golden: list[GoldenAnswer],
    db_connection: asyncpg.Connection[Any],
    tmp_path: Path,
) -> Manifest:
    return await build_slice(
        db_connection, golden, SliceConfig(seasons=(seasons[0],)), tmp_path / "slice"
    )


@pytest.fixture
async def empty_database_url(postgres_url: str) -> AsyncIterator[str]:
    """Свежая пустая база в том же контейнере: на ней загрузчик начинает «с нуля»."""
    admin = await asyncpg.connect(asyncpg_dsn(postgres_url))
    name = "slice_target"
    await admin.execute(f"DROP DATABASE IF EXISTS {name}")
    await admin.execute(f"CREATE DATABASE {name}")
    try:
        yield postgres_url.rsplit("/", 1)[0] + f"/{name}"
    finally:
        await admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        await admin.close()


class TestСборка:
    async def test_в_срез_идут_сезоны_конфига_вопросов_и_последний(
        self, built: Manifest, seasons: list[str]
    ) -> None:
        assert set(built.seasons) == {seasons[0], seasons[1], seasons[-1]}
        assert built.source_seasons == tuple(seasons)

    async def test_таблицы_ограничены_сезонами_и_документами(
        self, built: Manifest, db_connection: asyncpg.Connection[Any]
    ) -> None:
        in_slice = await db_connection.fetchval(
            "SELECT count(*) FROM box_scores WHERE season = ANY($1::text[])", list(built.seasons)
        )
        total = await db_connection.fetchval("SELECT count(*) FROM box_scores")

        assert built.table("box_scores").rows == in_slice < total
        assert built.table("games").rows == await db_connection.fetchval(
            "SELECT count(*) FROM games WHERE season = ANY($1::text[])", list(built.seasons)
        )
        # Игроки и команды — целиком: резолвер имён обязан видеть тех же однофамильцев.
        assert built.table("players").rows == await db_connection.fetchval(
            "SELECT count(*) FROM players"
        )
        assert built.documents == ("NBA Rules", "LeBron James")
        assert built.table("chunks").rows == 2
        assert built.embedding_models == (EMBEDDER,)

    async def test_пересборка_побайтово_одинакова(
        self,
        built: Manifest,
        golden: list[GoldenAnswer],
        seasons: list[str],
        db_connection: asyncpg.Connection[Any],
        tmp_path: Path,
    ) -> None:
        again = await build_slice(
            db_connection, golden, SliceConfig(seasons=(seasons[0],)), tmp_path / "again"
        )

        assert again == built
        assert (tmp_path / "slice" / MANIFEST_NAME).read_bytes() == (
            tmp_path / "again" / MANIFEST_NAME
        ).read_bytes()

    async def test_манифест_читается_с_диска(self, built: Manifest, tmp_path: Path) -> None:
        assert read_manifest(tmp_path / "slice") == built

    async def test_статьи_нет_в_базе_срез_не_собирается(
        self,
        corpus: None,
        db_connection: asyncpg.Connection[Any],
        seasons: list[str],
        tmp_path: Path,
    ) -> None:
        golden = [wiki_answer("bio", "Кто?", "Нет Такого")]

        with pytest.raises(SliceBuildError, match="Нет Такого"):
            await build_slice(db_connection, golden, SliceConfig(), tmp_path)

    async def test_без_rulebook_срез_не_собирается(
        self,
        seeded_database: None,
        db_connection: asyncpg.Connection[Any],
        tmp_path: Path,
    ) -> None:
        with pytest.raises(SliceBuildError, match="rulebook"):
            await build_slice(db_connection, [answer("a", "q")], SliceConfig(), tmp_path)

    async def test_сверх_бюджета_падает_но_файлы_остаются(
        self,
        corpus: None,
        golden: list[GoldenAnswer],
        db_connection: asyncpg.Connection[Any],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(dump, "BUDGET_BYTES", 1)

        with pytest.raises(BudgetExceededError, match="GitHub Release"):
            await build_slice(db_connection, golden, SliceConfig(), tmp_path / "big")

        assert (tmp_path / "big" / MANIFEST_NAME).exists()


class TestЗагрузка:
    async def test_срез_заливается_в_чистую_базу_с_миграциями(
        self, built: Manifest, empty_database_url: str, tmp_path: Path
    ) -> None:
        await prepare_slice(empty_database_url, tmp_path / "slice")

        target = await asyncpg.connect(asyncpg_dsn(empty_database_url))
        try:
            for table in built.tables:
                assert await target.fetchval(f"SELECT count(*) FROM {table.name}") == table.rows
            # Вектор доехал как есть, tsvector досчитала база.
            chunk = await target.fetchrow(
                "SELECT embedding::text AS vector, ts::text AS ts, embedding_model "
                "FROM chunks WHERE text LIKE '%LeBron%'"
            )
            assert chunk is not None
            assert chunk["vector"].startswith("[0,0.25,0,0")
            assert "lebron" in chunk["ts"]
            assert chunk["embedding_model"] == EMBEDDER
            # Identity подвинут за максимальный id: следующая вставка не упирается в дубль.
            max_id = await target.fetchval("SELECT max(id) FROM documents")
            new_id = await target.fetchval(
                "INSERT INTO documents (source_type, title, content_hash, fetched_at) "
                "VALUES ('wikipedia', 'Новая', 'h', now()) RETURNING id"
            )
            assert new_id > max_id
        finally:
            await target.close()

    async def test_повторная_загрузка_идемпотентна(
        self, built: Manifest, empty_database_url: str, tmp_path: Path
    ) -> None:
        await prepare_slice(empty_database_url, tmp_path / "slice")
        await prepare_slice(empty_database_url, tmp_path / "slice")

        target = await asyncpg.connect(asyncpg_dsn(empty_database_url))
        try:
            assert await slice_state(target, built) == "loaded"
            assert await target.fetchval("SELECT count(*) FROM games") == built.table("games").rows
        finally:
            await target.close()

    async def test_в_базу_с_чужими_данными_не_грузит(
        self, built: Manifest, postgres_url: str, tmp_path: Path
    ) -> None:
        # Тестовая база содержит фикстуру датасета целиком — это «полная база разработчика».
        with pytest.raises(SliceLoadError, match="не Eval-срез"):
            await prepare_slice(postgres_url, tmp_path / "slice")

    async def test_битый_дамп_отвергается_до_загрузки(
        self, built: Manifest, empty_database_url: str, tmp_path: Path
    ) -> None:
        broken = tmp_path / "broken"
        shutil.copytree(tmp_path / "slice", broken)
        (broken / "teams.copy.gz").write_bytes(b"not a dump")

        with pytest.raises(SliceLoadError, match="контрольная сумма"):
            await prepare_slice(empty_database_url, broken)

        target = await asyncpg.connect(asyncpg_dsn(empty_database_url))
        try:
            assert await target.fetchval("SELECT count(*) FROM teams") == 0
        finally:
            await target.close()

    async def test_после_загрузки_зовётся_хук_производных(
        self,
        built: Manifest,
        empty_database_url: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        called: list[str] = []

        async def fingerprints(_: Any) -> None:
            called.append("fingerprints")

        monkeypatch.setattr(derived, "REBUILDS", (("fingerprints", fingerprints),))

        await prepare_slice(empty_database_url, tmp_path / "slice")

        assert called == ["fingerprints"]


class TestПрогонНаСрезе:
    def test_сезон_которого_нет_в_срезе_ловится_до_прогона(self, tmp_path: Path) -> None:
        manifest = _manifest(seasons=("2012-13",), source_seasons=("2012-13", "2013-14"))

        with pytest.raises(SliceLoadError, match="пересобери"):
            check_run_fits_slice(
                manifest, [answer("a", "Очки в 2013-14?")], embedding_model=EMBEDDER
            )

    def test_чужой_эмбеддер_ловится_до_прогона(self) -> None:
        manifest = _manifest()

        with pytest.raises(SliceLoadError, match="EMBEDDING_MODEL"):
            check_run_fits_slice(manifest, [], embedding_model="other/model")

    def test_подходящий_срез_проходит(self) -> None:
        check_run_fits_slice(
            _manifest(), [answer("a", "Очки в 2012-13?")], embedding_model=EMBEDDER
        )

    async def test_устаревший_срез_роняет_run_до_подключения_к_базе(self, tmp_path: Path) -> None:
        directory = tmp_path / "slice"
        directory.mkdir()
        (directory / MANIFEST_NAME).write_text(
            _manifest(seasons=(), source_seasons=("2012-13",)).model_dump_json(), "utf-8"
        )
        answers = tmp_path / "answers.yaml"
        answers.write_text(
            "- id: a\n  category: stats\n  question: Очки в 2012-13?\n  reference: [x]\n",
            "utf-8",
        )
        settings = Settings(database_url="postgresql+asyncpg://nobody@127.0.0.1:1/none")

        with pytest.raises(SliceLoadError, match="устарел"):
            await run_answer_eval(
                settings,
                categories=None,
                ids=None,
                answers_path=answers,
                thresholds_path=None,
                report_dir=tmp_path / "reports",
                concurrency=1,
                slice_dir=directory,
            )


def _manifest(
    seasons: tuple[str, ...] = ("2012-13",),
    source_seasons: tuple[str, ...] = ("2012-13",),
) -> Manifest:
    return Manifest(
        alembic_revision="x",
        embedding_models=(EMBEDDER,),
        seasons=seasons,
        source_seasons=source_seasons,
        documents=(),
        tables=(),
    )
