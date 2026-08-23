# Design: Спринт 2 — RAG и цитаты

Source: [roadmap.md § Спринт 2](../roadmap.md) (решения гриля 2026-08-23), PRD — issue #13. Status: reviewed.

Build-time артефакт: пути и сигнатуры устаревают вместе с реализацией, документ живёт один спринт.

## Slice 1: Seed до основания NBA

**Write-set:** `backend/src/court_vision/seed/rows.py`, `players/models.py`, `players/schemas.py`,
`players/service.py`, `agent/prompts/tools/get_player_stats.md`, миграция, `scripts/build_fixture.py`,
`tests/fixtures/dataset/`, тесты.
**Depends on:** none. Идёт первым: список статей для корпуса берётся из расширенной таблицы `players`.

### Спайк (до кода)

Скрипт в scratchpad: по декадам — число матчей, игроков, доля пустых значений по каждой метрике.
Итог — граница `FIRST_SEASON` (цель `1946-47`; при грязи до ~1970 — отступ, факт в deferred.md).

### Signatures

```python
# seed/rows.py
FIRST_SEASON: Final = "1946-47"          # или граница из спайка
METRIC_INTRODUCED: Final[dict[str, str]] = {   # сезон введения метрики
    "minutes": "1951-52",
    "rebounds_offensive": "1973-74", "rebounds_defensive": "1973-74",
    "steals": "1973-74", "blocks": "1973-74", "turnovers": "1977-78",
    "three_pointers_made": "1979-80", "three_pointers_attempted": "1979-80",
}
def metric_tracked(metric: str, season: str) -> bool
```

```python
# players/models.py — миграция: эра-зависимые колонки box_scores становятся NULLable
# (steals, blocks, turnovers, rebounds_offensive/defensive, three_pointers_*; minutes уже NULLable)

# players/schemas.py — соответствующие поля SeasonTotals/SeasonAverages -> int | None / float | None
# None сериализуется как null; в описании инструмента: null = «метрика не считалась в ту эпоху», не ноль
```

### Call stack (main path)

```
python -m court_vision.seed
└─ seed_database()                 existing — фильтр эры пропускает всё с FIRST_SEASON
   └─ box_score_row()              EDIT: parse_int_or_zero -> parse_int для эра-зависимых метрик
get_player_stats("Wilt Chamberlain")
└─ season_stats()                  existing — SUM/AVG по NULL дают NULL, не 0
```

### Test shape

```
test_seed_rows.py   += box_score_row сезона 1961-62 -> blocks is None (не 0)
test_player_stats.py+= тоталы старой эры: blocks=null; современной: без null
fixtures            += игрок старой эры (спайк подскажет id) в PLAYER_IDS + его сезоны в SEASONS
```

**Done when:** полный seed проходит локально; на игроке старой эры инструмент отдаёт `null`,
а не `0`, по невведённым метрикам; тесты зелёные.

## Slice 2: Корпус — схема и ingestion CLI

**Write-set:** `backend/src/court_vision/corpus/` (NEW: `models.py`, `sources.py`, `chunking.py`,
`ingest.py`, `__main__.py`), `agent/transport.py` (+embed), `config.py`, миграция, `models.py` (реэкспорт),
`pyproject.toml` (+`pgvector`, `pypdf`).
**Depends on:** slice 1 (список игроков), но код пишется параллельно — зависимость только у запуска.

### File placement

```
backend/src/court_vision/corpus/
├── models.py        NEW  Document, Chunk
├── sources.py       NEW  выгрузка: rulebook PDF, Wikipedia API
├── chunking.py      NEW  структурный чанкинг с локаторами
├── ingest.py        NEW  оркестрация: diff по хешу -> чанки -> эмбеддинги -> upsert
└── __main__.py      NEW  CLI: python -m court_vision.corpus [--source rulebook|wikipedia] [--reindex]
```

### Signatures

```python
# corpus/models.py
SourceType = Literal["rulebook", "wikipedia"]
class Document(Base):        # "documents"
    id: int; source_type: str          # String(16), idx
    title: str; url: str | None        # url — стабильный внешний id
    content_hash: str                  # String(64), sha256 — ключ идемпотентности
    fetched_at: datetime
    # unique (source_type, title)
class Chunk(Base):           # "chunks"
    id: int; document_id: int          # FK CASCADE
    locator: str                       # "Rule 5 — Sec. II" / "Playing career > Chicago Bulls"
    text: str
    embedding: Vector(768)             # pgvector; HNSW (vector_cosine_ops)
    embedding_model: str               # String(64) — индекс знает, чем посчитан
    ts: TSVECTOR                       # generated always as to_tsvector('english', text), GIN

# corpus/sources.py
@dataclass(frozen=True) class RawDocument:
    source_type: SourceType; title: str; url: str | None; text: str  # уже плоский текст с заголовками
def fetch_rulebook(cache_dir: Path) -> RawDocument                   # запиненный URL+sha256 PDF
def iter_wikipedia(titles: Iterable[str]) -> Iterator[RawDocument]   # REST API, секции; 404 -> skip+лог
async def wikipedia_titles(session) -> list[str]                     # игроки+команды из БД + сезоны NBA

# corpus/chunking.py
@dataclass(frozen=True) class ChunkDraft:
    locator: str; text: str; embed_text: str   # embed_text = "title > locator" + text
MAX_CHUNK_TOKENS: Final = 500
def chunk_document(doc: RawDocument) -> list[ChunkDraft]   # по структуре; инфобоксы/таблицы выброшены

# agent/transport.py — эмбеддинги тем же транспортом, с учётом в llm_usage (operation="corpus.embed")
async def embed(session_factory, *, model: str, texts: Sequence[str], dimensions: int,
                timeout: float, operation: str, api_key: str | None, api_base: str | None
                ) -> list[list[float]]                      # litellm.aembedding, батчами

# config.py — Settings +=
embedding_model: str = "openai/google/gemini-embedding-001"
embedding_dimensions: int = 768
```

### Call stack (main path)

```
python -m court_vision.corpus
└─ ingest(session_factory, settings, source=None, reindex=False)
   ├─ fetch_rulebook() / iter_wikipedia(wikipedia_titles())
   ├─ diff: content_hash совпал -> skip            (идемпотентность, докачка после обрыва)
   ├─ chunk_document() -> embed(batch)             отказ, если в индексе чужая embedding_model
   └─ upsert Document + delete/insert его Chunks   (одна транзакция на документ)
```

### Test shape

```
test_corpus_chunking.py   фикстурные PDF-страницы/wiki-текст -> локаторы и границы чанков
test_corpus_ingest.py     повторный ingest без изменений -> 0 записей; смена hash -> переиндексация
                          чужая embedding_model без --reindex -> отказ   (embed замокан как litellm)
```

**Done when:** локальный прогон индексирует rulebook + Wikipedia-срез; повторный прогон — no-op;
`select count(*) from chunks` соответствует ожиданиям; тесты зелёные.

## Slice 3: Гибридный поиск

**Write-set:** `backend/src/court_vision/corpus/search.py` (NEW), `corpus/rerank.py` (NEW), `config.py`.
**Depends on:** slice 2 (схема chunks).

### Signatures

```python
# corpus/search.py
@dataclass(frozen=True) class ChunkHit:
    chunk_id: int; document_title: str; source_type: SourceType
    locator: str; text: str; url: str | None; score: float
RRF_K: Final = 60; CANDIDATES: Final = 50
async def search(session, *, query_embedding: list[float], query_text: str,
                 source: SourceType | None, top_k: int) -> list[ChunkHit]
# один SQL: (top-50 по cosine) FULL OUTER JOIN (top-50 по ts_rank) -> RRF -> limit greatest(top_k, 30)

# corpus/rerank.py — выключаемая ступень
async def maybe_rerank(hits: list[ChunkHit], *, query: str, settings: Settings) -> list[ChunkHit]
# settings.rerank_api_key пуст -> hits как есть; иначе litellm.arerank(jina) поверх топ-30 -> топ-top_k
# отказ Jina -> лог + порядок RRF (поиск не падает из-за реранкера)

# config.py — Settings +=
rerank_api_key: str | None = None
rerank_model: str = "jina_ai/jina-reranker-v2-base-multilingual"
rag_top_k: int = 8
```

### Test shape

```
test_corpus_search.py   мини-корпус с готовыми векторами (без API):
├─ векторный находит парафраз, полнотекстовый — точный термин, RRF содержит оба
├─ source="rulebook" не возвращает wikipedia-чанков
└─ maybe_rerank без ключа -> порядок не тронут; с замоканным litellm.arerank -> пересортировка;
   упавший arerank -> порядок RRF, не исключение
```

**Done when:** тесты зелёные; ручной прогон на реальном индексе возвращает осмысленный топ.

## Slice 4: Инструмент `search_corpus` и цитаты насквозь

**Write-set:** `agent/tools.py`, `agent/loop.py`, `agent/evidence.py`, `agent/prompts/system.md`,
`agent/prompts/tools/search_corpus.md` (NEW), `conversations/schemas.py`, `conversations/streaming.py`,
`conversations/models.py` (+миграция), `conversations/service.py`;
frontend: `shared/api/wire.ts` (+regen `schema.d.ts`), `features/chat/model/types.ts`,
`api/answerStream.ts`, `api/conversations.ts`, `model/useConversationStream.ts`, `ui/EvidenceCard.tsx`.
**Depends on:** slice 3.

### Signatures — backend

```python
# agent/tools.py
class SearchCorpusArgs(BaseModel):
    query: str = Field(min_length=1, max_length=256)   # описание требует английский запрос
    source: Literal["rulebook", "wikipedia"] | None = None   # «эксперт по правилам» = source="rulebook"
async def run_search_corpus(session, arguments, ctx: ToolContext) -> ToolResult
# результат модели: {"results": [{"n": 7, "title", "locator", "text", "url"}]}
# n — сквозной номер цитирования, выдаёт ctx.citations; system.md: «цитируй как [n]»

# agent/loop.py — контекст хода: сквозная нумерация цитат через все вызовы search_corpus
@dataclass class ToolContext:
    citations: CitationRegistry
class CitationRegistry:
    def register(self, hit: ChunkHit) -> int           # 1, 2, 3... в порядке регистрации, дедуп по chunk_id
    def cited_in(self, text: str) -> list[TurnCitation] # маркеры [n] из финального текста -> факты цитат
@dataclass class TurnCitation:
    index: int; tool_call_id: str; chunk_id: int
    document_title: str; locator: str; snippet: str; source_url: str | None
# AgentAnswer += citations: tuple[TurnCitation, ...]
# Tool.run получает ctx третьим аргументом (get_player_stats его игнорирует)

# agent/evidence.py
class QuotesView(BaseModel):                            # проекция search_corpus в карточку
    kind: Literal["quotes"] = "quotes"
    quotes: list[Quote]                                 # Quote{index, title, locator, text}
def project_search_corpus(result) -> EvidenceView | None

# conversations/schemas.py — CitationEvent приводится к фактической форме
class CitationEvent(BaseModel):
    type: Literal["citation"] = "citation"
    index: int; tool_call_id: str; chunk_id: int
    document_title: str; locator: str; snippet: str; source_url: str | None = None
# MessageResponse += citations: list[CitationEvent] = []   (у user-реплик пусто)

# conversations/models.py — Message += citations: JSONB | None; миграция
# conversations/streaming.py — _save_answer(text, citations) в ту же транзакцию;
#   после AgentAnswer: yield CitationEvent(...) для каждой цитаты, затем DoneEvent
```

### Signatures — frontend

```ts
// model/types.ts — Citation расширяется до факта цитаты (совместимо с Message.tsx: index остаётся)
type Citation = { index: number; toolCallId: string; chunkId: number;
                  documentTitle: string; locator: string; snippet: string; sourceUrl?: string }
// api/answerStream.ts — case 'citation' перестаёт возвращать null
// api/conversations.ts — toMessage читает message.citations вместо жёсткого []
// ui/EvidenceCard.tsx — ветка kind="quotes": список цитат с mono-локаторами и номерами [n]
```

### Call stack (main path)

```
POST /conversations/{id}/messages  «what is goaltending?»
└─ stream_answer()
   ├─ модель зовёт search_corpus(query="goaltending rule", source="rulebook")
   │  └─ run_search_corpus()
   │     ├─ transport.embed(query)                     slice 2
   │     ├─ corpus.search() -> maybe_rerank()          slice 3
   │     └─ ctx.citations.register(hit) -> n           нумерация
   ├─ ToolCalled / ToolResulted(view=QuotesView)       existing путь событий
   ├─ TokenChunk...  модель пишет «...offensive interference [1]»
   └─ AgentAnswer(citations=registry.cited_in(text))
      └─ streaming: CitationEvent × N -> _save_answer(text, citations) -> DoneEvent
```

Валидация постфактум: маркер без зарегистрированного номера остаётся плоским текстом
(фронт уже так делает); номер без маркера в тексте не эмитится.

### Test shape

```
test_agent_loop.py      += search_corpus: вызов -> QuotesView; маркеры в ответе -> AgentAnswer.citations;
                           маркер-сирота не попадает в citations; два вызова -> нумерация сквозная
test_conversations.py   += поток содержит citation до done; citations сохранены и
                           возвращаются в GET /messages; событие описано в OpenAPI
frontend Message/stream += citation-событие делает маркер кликабельным; reload диалога
                           восстанавливает маркеры из message.citations
```

**Done when:** демо-сценарий спринта проходит end-to-end: вопрос про правило → ответ с
кликабельными `[n]` → карточка с цитатами rulebook; после F5 маркеры живы.

## Slice 5: Ретривал-eval

**Write-set:** `backend/evals/` (NEW: `golden.yaml`, `fixture_corpus.jsonl`), `corpus/eval.py` (NEW),
`tests/test_retrieval_eval.py`, `.github/workflows/ci.yml` (без изменений — едет в `pytest`).
**Depends on:** slice 3.

### Устройство

Два режима одного кода:
- **CI (детерминированный):** фикстурный корпус с *предвычисленными* векторами
  (`fixture_corpus.jsonl`: chunk + embedding float32, округлённый; rulebook целиком + ~20 wiki-статей)
  и предвычисленные векторы золотых запросов в `golden.yaml`. Ни одного API-вызова: гоняется как
  обычный pytest против testcontainers-Postgres.
- **Локально (реальный индекс):** `python -m court_vision.corpus --eval` — живые эмбеддинги запросов
  против полного индекса; этим же флагом сравнивается реранкер on/off.

```python
# corpus/eval.py
@dataclass(frozen=True) class GoldenQuery:
    query: str; source: SourceType | None
    expect_document: str; expect_locator: str | None    # локатор опционален: попадание в документ уже сигнал
    embedding: list[float] | None                       # None -> посчитать через API (локальный режим)
@dataclass(frozen=True) class EvalReport:
    recall_at_10: float; mrr: float; by_source: dict[str, tuple[float, float]]
async def evaluate(session, queries: Sequence[GoldenQuery], *, settings, rerank: bool) -> EvalReport
```

```
golden.yaml         ~40 запросов: ~20 rulebook (goaltending, clear path, shot clock...),
                    ~20 wikipedia (биографии, эпохи, «who won the 1998 finals»)
```

### Test shape

```
test_retrieval_eval.py
├─ загрузка fixture_corpus -> evaluate() -> recall@10 >= ПОРОГ, mrr >= ПОРОГ  (CI-гейт)
└─ разбивка by_source присутствует для обоих источников
ПОРОГ зафиксирован константой после первого прогона бейзлайна (правило из roadmap)
```

**Done when:** eval живёт в CI и валит сборку при деградации; локальный прогон печатает
таблицу on/off реранкера — по ней принято решение, включать ли Jina по умолчанию.

## Open questions

Нет — продуктовые ветки закрыты грилем 2026-08-23.
