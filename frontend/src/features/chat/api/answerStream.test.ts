import { describe, expect, it } from 'vitest'

import { createAnswerStream, toStreamEvent } from '@/features/chat/api/answerStream'
import type { StreamEvent } from '@/features/chat/model/types'
import type { ApiClient } from '@/shared/api/client'
import type { ChatStreamEvent } from '@/shared/api/wire'

describe('toStreamEvent', () => {
  it('переводит токен в дописывание текста', () => {
    expect(toStreamEvent({ type: 'token', text: '26.8' })).toEqual({ type: 'token', text: '26.8' })
  })

  it('открывает карточку Evidence на вызове Инструмента — ещё без результата', () => {
    // Событие приходит до выполнения: результата в этот момент нет ни у кого, а карточка
    // уже нужна — она и есть «Агент пошёл за данными».
    const mapped = toStreamEvent({
      type: 'tool_call',
      tool_call_id: 'call_1',
      name: 'get_player_stats',
      arguments: { player_name: 'LeBron James', season: '2012-13' },
    })

    expect(mapped).toEqual({
      type: 'tool_call',
      toolCall: {
        id: 'call_1',
        name: 'get_player_stats',
        source: 'LeBron James · 2012-13',
      },
    })
  })

  it('дописывает в карточку результат Инструмента и длительность', () => {
    const mapped = toStreamEvent({
      type: 'tool_result',
      tool_call_id: 'call_1',
      duration_ms: 174,
      error: null,
      view: {
        kind: 'table',
        columns: ['Season', 'GP', 'Pts', 'Reb', 'Ast', 'FG%'],
        rows: [['2012-13', '76', '26.8', '8.0', '7.3', '56.5']],
      },
    })

    expect(mapped).toEqual({
      type: 'tool_result',
      outcome: {
        id: 'call_1',
        durationMs: 174,
        result: {
          kind: 'table',
          columns: ['Season', 'GP', 'Pts', 'Reb', 'Ast', 'FG%'],
          rows: [['2012-13', '76', '26.8', '8.0', '7.3', '56.5']],
        },
      },
    })
  })

  it('отказ Инструмента доезжает исходом, а не пустой карточкой', () => {
    // Иначе вызов, на котором данных не нашлось, выглядит в рейке как удачный.
    const mapped = toStreamEvent({
      type: 'tool_result',
      tool_call_id: 'call_2',
      duration_ms: 12,
      error: { kind: 'season-data-missing', detail: 'Данных за сезон 2005-06 нет.' },
      view: null,
    })

    expect(mapped).toEqual({
      type: 'tool_result',
      outcome: {
        id: 'call_2',
        durationMs: 12,
        error: { kind: 'season-data-missing', detail: 'Данных за сезон 2005-06 нет.' },
      },
    })
  })

  it('вызов без аргументов остаётся карточкой, а не пропадает', () => {
    // Аргументы могут не разобраться на стороне бэкенда, но вызов состоялся, и в
    // провенансе он обязан быть виден.
    const mapped = toStreamEvent({
      type: 'tool_call',
      tool_call_id: 'call_2',
      name: 'get_player_stats',
      arguments: null,
    })

    expect(mapped).toMatchObject({ type: 'tool_call', toolCall: { source: 'no arguments' } })
  })

  it('исход без карточки не роняет поток', () => {
    // Подписались посреди ответа — вызова с таким id в рейке нет. Событие применяется в
    // никуда, но поток от этого не рвётся.
    expect(
      toStreamEvent({
        type: 'tool_result',
        tool_call_id: 'call_unknown',
        duration_ms: 3,
        error: null,
        view: null,
      }),
    ).toEqual({ type: 'tool_result', outcome: { id: 'call_unknown', durationMs: 3 } })
  })

  it('ошибку отдаёт вместе с телом Problem Details', () => {
    const problem = {
      type: 'urn:court-vision:problem:llm-provider-unavailable',
      title: 'LLM provider unavailable',
      status: 502,
      detail: 'LLM-провайдер не ответил на запрос.',
      instance: '/api/v1/conversations/x/messages',
      cause: 'APIConnectionError',
    }

    expect(toStreamEvent({ type: 'error', problem })).toEqual({ type: 'error', problem })
  })

  it('цитата приезжает фактом целиком: маркер получает карточку и текст источника', () => {
    const citation: ChatStreamEvent = {
      type: 'citation',
      index: 1,
      tool_call_id: 'call_corpus',
      chunk_id: 41,
      document_title: 'NBA rulebook',
      locator: 'Rule 11 — Sec. I',
      snippet: 'Section II.',
      source_url: 'https://official.nba.com/rulebook',
    }

    expect(toStreamEvent(citation)).toEqual({
      type: 'citation',
      citation: {
        index: 1,
        toolCallId: 'call_corpus',
        chunkId: 41,
        documentTitle: 'NBA rulebook',
        locator: 'Rule 11 — Sec. I',
        snippet: 'Section II.',
        sourceUrl: 'https://official.nba.com/rulebook',
      },
    })
  })

  it('цитата без адреса источника не тащит null в модель', () => {
    // Кешированный файл без стабильного URL: поле просто отсутствует, а не равно null.
    const mapped = toStreamEvent({
      type: 'citation',
      index: 2,
      tool_call_id: 'call_corpus',
      chunk_id: 42,
      document_title: 'NBA rulebook',
      locator: 'Rule 11 — Sec. II',
      snippet: 'Section III.',
      source_url: null,
    })

    expect(mapped?.type === 'citation' && 'sourceUrl' in mapped.citation).toBe(false)
  })

  it('неизвестный тип события не роняет поток', () => {
    // Контракт может обогнать клиент: событие из будущего просто игнорируется.
    expect(toStreamEvent({ type: 'thinking' } as unknown as ChatStreamEvent)).toBeNull()
  })
})

interface StubCall {
  path: string
  body: unknown
}

function stubClient(events: ChatStreamEvent[], calls: StubCall[]): ApiClient {
  return {
    get: () => Promise.reject(new Error('не ожидалось')),
    post: (path, body) => {
      calls.push({ path, body })
      return Promise.resolve({ conversation_id: 'created-id' } as never)
    },
    stream: <EventT>(path: string, body: unknown): AsyncIterable<EventT> => {
      calls.push({ path, body })
      return (async function* () {
        for (const event of events) {
          yield event as EventT
        }
      })()
    },
  }
}

async function collect(source: AsyncIterable<StreamEvent>): Promise<StreamEvent[]> {
  const events: StreamEvent[] = []
  for await (const event of source) {
    events.push(event)
  }
  return events
}

describe('createAnswerStream', () => {
  const answer: ChatStreamEvent[] = [
    { type: 'token', text: '26.8' },
    { type: 'done', message_id: 7 },
  ]

  it('в существующем Диалоге сразу шлёт вопрос, не создавая новый', async () => {
    const calls: StubCall[] = []
    const stream = createAnswerStream({
      client: stubClient(answer, calls),
      conversation: { current: 'existing-id' },
    })

    const events = await collect(stream('Сколько набирал Леброн?'))

    expect(events).toEqual([{ type: 'token', text: '26.8' }, { type: 'done' }])
    expect(calls).toEqual([
      {
        path: '/api/v1/conversations/existing-id/messages',
        body: { content: 'Сколько набирал Леброн?' },
      },
    ])
  })

  it('новый Диалог создаётся первым вопросом и переиспользуется вторым', async () => {
    // Иначе второй вопрос уезжал бы в свежий Диалог — без истории первого, то есть
    // follow-up «а в 2015-16?» терял бы имя игрока.
    const calls: StubCall[] = []
    const created: string[] = []
    const conversation = { current: undefined }
    const stream = createAnswerStream({
      client: stubClient(answer, calls),
      conversation,
      onConversationCreated: (id) => {
        created.push(id)
      },
    })

    await collect(stream('Первый вопрос'))
    await collect(stream('А в 2015-16?'))

    expect(created).toEqual(['created-id'])
    expect(calls.map((call) => call.path)).toEqual([
      '/api/v1/conversations',
      '/api/v1/conversations/created-id/messages',
      '/api/v1/conversations/created-id/messages',
    ])
  })

  it('пересозданный поток продолжает тот же Диалог, а не заводит второй', async () => {
    // Клиент пересоздаётся сам собой: Clerk обновляет сессионный токен посреди разговора.
    // Идентификатор Диалога живёт в ref и такое обновление переживает.
    const calls: StubCall[] = []
    const conversation = { current: undefined }
    const first = createAnswerStream({ client: stubClient(answer, calls), conversation })
    await collect(first('Первый вопрос'))

    const second = createAnswerStream({ client: stubClient(answer, calls), conversation })
    await collect(second('А в 2015-16?'))

    expect(calls.filter((call) => call.path === '/api/v1/conversations')).toHaveLength(1)
  })

  it('отмена доезжает до транспорта', async () => {
    // Без этого брошенный ответ висит открытым запросом до конца генерации на сервере.
    const signals: (AbortSignal | undefined)[] = []
    const client: ApiClient = {
      get: () => Promise.reject(new Error('не ожидалось')),
      post: () => Promise.reject(new Error('не ожидалось')),
      stream: <EventT>(_path: string, _body: unknown, signal?: AbortSignal) => {
        signals.push(signal)
        return (async function* () {
          yield { type: 'done', message_id: 1 } as EventT
        })()
      },
    }
    const abort = new AbortController()

    await collect(
      createAnswerStream({ client, conversation: { current: 'id' } })('Вопрос', {
        signal: abort.signal,
      }),
    )

    expect(signals).toEqual([abort.signal])
  })
})
