/** Подставной API на уровне `fetch`.
 *
 * Подменяется именно `fetch`, а не модуль клиента: тогда в тестах работает настоящий
 * `createApiClient` — заголовок `Authorization`, разбор Problem Details и парсер SSE. Мок
 * на уровне модуля проверял бы только вызовы компонентов, а весь транспорт оставался бы
 * непокрытым ровно в том срезе, где он и появился.
 */

import { vi } from 'vitest'

import type { ChatStreamEvent, ConversationSummary, MessageResponse } from '@/shared/api/wire'

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
}

export interface ApiStubOptions {
  conversations?: ConversationSummary[]
  messages?: MessageResponse[]
  events?: ChatStreamEvent[]
  /** Диалоги, которых «нет»: обращение к ним отдаёт 404 Problem Details. */
  missing?: string[]
}

export function installApiStub(options: ApiStubOptions = {}): ApiStub {
  const {
    conversations = [SAVED_CONVERSATION],
    messages = SAVED_MESSAGES,
    events = ANSWER_EVENTS,
    missing = [],
  } = options

  const stub: ApiStub = { calls: [], lastAuthorization: null }

  vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://api.test')
    stub.calls.push(`${init?.method ?? 'GET'} ${url.pathname}`)
    stub.lastAuthorization = new Headers(init?.headers).get('Authorization')

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
function sse(events: ChatStreamEvent[]): Response {
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(': ping\r\n\r\n'))
      for (const event of events) {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\r\n\r\n`))
      }
      controller.close()
    },
  })
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}
