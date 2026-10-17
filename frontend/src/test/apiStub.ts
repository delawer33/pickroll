/** Подставной API на уровне `fetch`.
 *
 * Подменяется именно `fetch`, а не модуль клиента: тогда в тестах работает настоящий
 * `createApiClient` — заголовок `Authorization`, разбор Problem Details и парсер SSE. Мок
 * на уровне модуля проверял бы только вызовы компонентов, а весь транспорт оставался бы
 * непокрытым ровно в том срезе, где он и появился.
 */

import { vi } from 'vitest'

import type {
  ChatStreamEvent,
  ConversationSummary,
  MessageResponse,
  ReportResponse,
  ReportStreamEvent,
  ReportSummary,
} from '@/shared/api/wire'

export const REPORT_ID = '22222222-2222-4222-8222-222222222222'

export const SAVED_REPORT: ReportResponse = {
  report_id: REPORT_ID,
  player: { player_id: 1628973, full_name: 'Jalen Brunson' },
  season: '2023-24',
  archetype: 'Scoring lead guard',
  summary: 'Brunson is an elite pull-up creator who carried New York.',
  created_at: '2026-10-01T09:00:00+00:00',
  schema_version: 1,
  model: 'test-model',
  content: {
    summary: 'Brunson is an elite pull-up creator who carried New York.',
    archetype: 'Scoring lead guard',
    strengths: [{ claim: 'Efficient scoring on high volume', metrics: ['ts_pct', 'pts'] }],
    weaknesses: [{ claim: 'Limited rim protection', metrics: ['blk'] }],
    stat_profile: 'Top-decile scoring, average rebounding.',
    comparables: [
      { player_name: 'Kyrie Irving', season: '2016-17', reason: 'Similar shot-making profile.' },
    ],
    outlook: 'Stays a primary option for years.',
  },
}

export const SAVED_REPORT_SUMMARY: ReportSummary = {
  report_id: SAVED_REPORT.report_id,
  player: SAVED_REPORT.player,
  season: SAVED_REPORT.season,
  archetype: SAVED_REPORT.archetype,
  summary: SAVED_REPORT.summary,
  created_at: SAVED_REPORT.created_at,
}

/** Поток заказа отчёта: два снапшота (раздел дописывается) и `done`. */
export const REPORT_STREAM_EVENTS: ReportStreamEvent[] = [
  { type: 'snapshot', report: {} },
  { type: 'snapshot', report: { summary: 'Brunson is an elite pull-up creator.' } },
  {
    type: 'snapshot',
    report: {
      summary: 'Brunson is an elite pull-up creator.',
      archetype: 'Scoring lead guard',
      strengths: [{ claim: 'Efficient scoring on high volume', metrics: ['ts_pct'] }],
    },
  },
  { type: 'done', report_id: REPORT_ID },
]

export const CONVERSATION_ID = '11111111-1111-4111-8111-111111111111'

const CREATED_AT = '2026-08-18T09:00:00+00:00'

export const SAVED_CONVERSATION: ConversationSummary = {
  // Заголовок короче первого вопроса: бэкенд обрезает его по границе слова, и в рейке
  // должно быть видно именно заголовок, а не всю Реплику.
  conversation_id: CONVERSATION_ID,
  title: 'Jokić’s passing since 2023',
  created_at: CREATED_AT,
}

export const SAVED_MESSAGES: MessageResponse[] = [
  {
    message_id: 1,
    role: 'user',
    content: 'How has Jokić’s passing changed since 2023?',
    created_at: CREATED_AT,
  },
  {
    message_id: 2,
    role: 'assistant',
    content: 'Volume is up: 9.8 assists per game against 8.4 two seasons ago.',
    created_at: CREATED_AT,
  },
]

/** Ответ Агента на новый вопрос — тот же порядок событий, что у настоящего потока. */
export const ANSWER_EVENTS: ChatStreamEvent[] = [
  {
    type: 'tool_call',
    tool_call_id: 'call_1',
    name: 'get_player_stats',
    arguments: { player_name: 'LeBron James', season: '2012-13' },
  },
  {
    type: 'tool_result',
    tool_call_id: 'call_1',
    duration_ms: 174,
    error: null,
    view: {
      kind: 'table',
      columns: ['Season', 'GP', 'Pts', 'Reb', 'Ast', 'FG%'],
      rows: [['2012-13', '76', '26.8', '8.0', '7.3', '56.5']],
    },
  },
  { type: 'token', text: 'LeBron averaged ' },
  { type: 'token', text: '26.8 points per game in 2012-13.' },
  { type: 'done', message_id: 42 },
]

export interface ApiStub {
  /** Запросы в порядке вызова, как `GET /api/v1/conversations` — по ним видно, что клиент
   * реально сходил в API и каким методом. */
  calls: string[]
  /** Токен, приложенный к последнему запросу. */
  lastAuthorization: string | null
  /** То же, что `calls`, но с query-строкой: по ней видно фильтр и курсор. */
  requests: string[]
}

export interface ApiStubOptions {
  conversations?: ConversationSummary[]
  messages?: MessageResponse[]
  events?: ChatStreamEvent[]
  /** Диалоги, которых «нет»: обращение к ним отдаёт 404 Problem Details. */
  missing?: string[]
  /** Свои отчёты пользователя для `GET /reports` (свежие сверху, как отдаёт бэкенд). */
  reports?: ReportSummary[]
  /** Отчёт для `GET /reports/{id}`; `null` — «чужой или несуществующий», 404. */
  report?: ReportResponse | null
  /** Потоки ответов `POST /reports` по порядку вызовов; последний повторяется. */
  reportStreams?: ReportStreamEvent[][]
  /** Отказ `POST /reports` ещё до потока (неопознанный игрок): Problem Details со статусом. */
  reportOrderRejected?: boolean
  /** Поток заказа не закрывается после событий: страница остаётся посреди генерации. */
  reportStreamHangs?: boolean
}

export function installApiStub(options: ApiStubOptions = {}): ApiStub {
  const {
    conversations = [SAVED_CONVERSATION],
    messages = SAVED_MESSAGES,
    events = ANSWER_EVENTS,
    missing = [],
    reports = [SAVED_REPORT_SUMMARY],
    report = SAVED_REPORT,
    reportStreams = [REPORT_STREAM_EVENTS],
    reportOrderRejected = false,
    reportStreamHangs = false,
  } = options

  const stub: ApiStub = { calls: [], lastAuthorization: null, requests: [] }
  let orders = 0

  vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://api.test')
    stub.calls.push(`${init?.method ?? 'GET'} ${url.pathname}`)
    stub.requests.push(`${init?.method ?? 'GET'} ${url.pathname}${url.search}`)
    stub.lastAuthorization = new Headers(init?.headers).get('Authorization')

    if (url.pathname.endsWith('/reports')) {
      if (init?.method === 'POST') {
        if (reportOrderRejected) {
          return Promise.resolve(
            problem(404, 'urn:court-vision:problem:player-not-found', 'Player not found'),
          )
        }
        const events = reportStreams[Math.min(orders, reportStreams.length - 1)] ?? []
        orders += 1
        return Promise.resolve(sse(events, { keepOpen: reportStreamHangs }))
      }
      const playerId = url.searchParams.get('player_id')
      const filtered = reports.filter(
        (item) => playerId === null || String(item.player.player_id) === playerId,
      )
      const limit = Number(url.searchParams.get('limit') ?? 20)
      const offset = Number(url.searchParams.get('cursor') ?? 0)
      const end = offset + limit
      return Promise.resolve(
        json({
          items: filtered.slice(offset, end),
          next_cursor: end < filtered.length ? String(end) : null,
        }),
      )
    }
    if (/\/reports\/[^/]+$/.test(url.pathname)) {
      return Promise.resolve(
        report === null
          ? problem(404, 'urn:court-vision:problem:report-not-found', 'Report not found')
          : json(report),
      )
    }

    const conversationId = url.pathname.match(/\/conversations\/([^/]+)/)?.[1]
    if (conversationId !== undefined && missing.includes(conversationId)) {
      return Promise.resolve(notFound(url.pathname))
    }

    if (url.pathname.endsWith('/messages')) {
      return Promise.resolve(
        init?.method === 'POST' ? sse(events) : json({ items: messages, next_cursor: null }),
      )
    }
    if (url.pathname.endsWith('/conversations')) {
      return Promise.resolve(
        init?.method === 'POST'
          ? json({ ...SAVED_CONVERSATION, title: 'Новый диалог' }, 201)
          : json({ items: conversations, next_cursor: null }),
      )
    }
    return Promise.resolve(notFound(url.pathname))
  })

  return stub
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function problem(status: number, type: string, title: string): Response {
  return new Response(
    JSON.stringify({ type, title, status, detail: `${title}.`, instance: '/api/v1/reports' }),
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  )
}

function notFound(path: string): Response {
  return new Response(
    JSON.stringify({
      type: 'urn:court-vision:problem:conversation-not-found',
      title: 'Conversation not found',
      status: 404,
      detail: 'Диалог не найден.',
      instance: path,
    }),
    { status: 404, headers: { 'Content-Type': 'application/problem+json' } },
  )
}

/** Тело SSE ровно в том виде, в каком его отдаёт FastAPI: фрейм на событие, `\r\n` в
 * разделителях и keepalive-комментарий между ними. */
function sse(
  events: Array<ChatStreamEvent | ReportStreamEvent>,
  { keepOpen = false }: { keepOpen?: boolean } = {},
): Response {
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(': ping\r\n\r\n'))
      for (const event of events) {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\r\n\r\n`))
      }
      if (!keepOpen) {
        controller.close()
      }
    },
  })
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}
