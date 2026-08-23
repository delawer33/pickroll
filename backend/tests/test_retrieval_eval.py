"""Ретривал-eval как CI-гейт: фикстурный корпус с предвычисленными векторами, ни одного
вызова API.

Векторы чанков и золотых запросов посчитаны одной hashing-trick схемой (генератор —
`scripts/build_eval_fixture.py`), так что косинусная близость отражает общий словарь.
Пороги зафиксированы после первого прогона бейзлайна (правило roadmap) с запасом ~0.05:
падение ниже — деградация поиска, а не шум.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from court_vision.config import Settings
from court_vision.corpus.eval import GoldenQuery, evaluate, load_golden
from court_vision.corpus.models import Chunk, Document, SourceType

BACKEND_ROOT = Path(__file__).resolve().parent.parent
EVALS_DIR = BACKEND_ROOT / "evals"

# Бейзлайн первого прогона (2026-08-23): recall@10 = 1.000, MRR = 0.958
# (rulebook 1.000/0.942, wikipedia 1.000/0.975).
# Порог — бейзлайн минус ~0.05 с округлением вниз: ниже — деградация, не шум.
RECALL_AT_10_THRESHOLD: Final = 0.95
MRR_THRESHOLD: Final = 0.90


def _load_golden() -> list[GoldenQuery]:
    return load_golden(EVALS_DIR / "golden.yaml")


@pytest.fixture
async def fixture_corpus(
    clean_database: None, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Фикстурный корпус в тестовом Postgres: документы и чанки с готовыми векторами."""
    lines = [
        cast(dict[str, object], json.loads(line))
        for line in (EVALS_DIR / "fixture_corpus.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    now = datetime.now(UTC)
    async with session_factory() as session:
        documents: dict[tuple[str, str], Document] = {}
        for line in lines:
            key = (cast(str, line["source_type"]), cast(str, line["title"]))
            if key not in documents:
                documents[key] = Document(
                    source_type=key[0],
                    title=key[1],
                    url=cast(str, line["url"]),
                    content_hash=f"fixture-{key[1]}",
                    fetched_at=now,
                )
                session.add(documents[key])
        await session.flush()
        session.add_all(
            Chunk(
                document_id=documents[
                    (cast(str, line["source_type"]), cast(str, line["title"]))
                ].id,
                locator=cast(str, line["locator"]),
                text=cast(str, line["text"]),
                embedding=cast(list[float], line["embedding"]),
                embedding_model="fixture/hashing-bow",
            )
            for line in lines
        )
        await session.commit()


class TestRetrievalEval:
    async def test_metrics_meet_thresholds(
        self, fixture_corpus: None, session: AsyncSession
    ) -> None:
        """CI-гейт: деградация поиска ниже порогов валит сборку."""
        report = await evaluate(session, _load_golden(), settings=Settings(), rerank=False)

        assert report.recall_at_10 >= RECALL_AT_10_THRESHOLD, report
        assert report.mrr >= MRR_THRESHOLD, report

    async def test_by_source_present_for_both_sources(
        self, fixture_corpus: None, session: AsyncSession
    ) -> None:
        report = await evaluate(session, _load_golden(), settings=Settings(), rerank=False)

        assert set(report.by_source) == {"rulebook", "wikipedia"}
        for recall, mrr in report.by_source.values():
            assert 0.0 < recall <= 1.0
            assert 0.0 < mrr <= 1.0

    async def test_shuffled_expectations_score_low(
        self, fixture_corpus: None, session: AsyncSession
    ) -> None:
        """Контроль различающей способности: eval с перепутанными ожиданиями обязан провалиться.

        Ожидания сдвигаются по кругу внутри источника (wiki-запросу приписывается чужая
        статья, rulebook-запросу — чужой локатор). Если бы векторы фикстуры не различали
        чанки, метрики не отличались бы от честного прогона — и гейт ничего бы не гейтил.
        """
        queries = _load_golden()
        by_source: dict[SourceType | None, list[GoldenQuery]] = {}
        for query in queries:
            by_source.setdefault(query.source, []).append(query)
        shuffled = [
            replace(
                query,
                expect_document=donor.expect_document,
                expect_locator=donor.expect_locator,
            )
            for group in by_source.values()
            for query, donor in zip(group, group[1:] + group[:1], strict=True)
        ]

        report = await evaluate(session, shuffled, settings=Settings(), rerank=False)

        assert report.recall_at_10 < 0.5, report
        assert report.mrr < 0.3, report
