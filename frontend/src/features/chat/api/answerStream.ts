/** Настоящий поток ответа Агента — на месте мока из #6.
 *
 * Шов остался тот же (`AnswerStream`), поменялось только то, что за ним стоит: события
 * приходят из SSE и переводятся в модель ленты. Компоненты этой замены не заметили.
 */

import { createConversation, toCitation } from '@/features/chat/api/conversations'
import type {
  AnswerStream,
  StreamEvent,
  ToolCall,
  ToolOutcome,
  ToolResult,
} from '@/features/chat/model/types'
import type { ApiClient } from '@/shared/api/client'
import type {
  ChatStreamEvent,
  EvidenceView,
  ToolCallEvent,
  ToolResultEvent,
} from '@/shared/api/wire'

/** Идентификатор Диалога, живущий дольше самого потока.
 *
 * Ref, а не переменная в замыкании: поток пересоздаётся, когда меняется API-клиент (Clerk
 * обновил сессионный токен — и это происходит сам собой посреди разговора), а Диалог при
 * этом остаётся тем же. В замыкании идентификатор при таком обновлении обнулялся бы, и
 * второй вопрос заводил бы второй Диалог, потеряв историю первого.
 */
export interface ConversationRef {
  current: string | undefined
}

export interface AnswerStreamOptions {
  client: ApiClient
  /** Диалог, в котором задаётся вопрос. Пустой ref — новый: он создастся первым вопросом. */
  conversation: ConversationRef
  /** Вызывается один раз, сразу после создания Диалога: списку в рейке пора обновиться. */
  onConversationCreated?: (conversationId: string) => void
}

export function createAnswerStream({
  client,
  conversation,
  onConversationCreated,
}: AnswerStreamOptions): AnswerStream {
  return async function* answerStream(
    question: string,
    options: { signal?: AbortSignal } = {},
  ): AsyncGenerator<StreamEvent> {
    if (conversation.current === undefined) {
      conversation.current = await createConversation(client)
      onConversationCreated?.(conversation.current)
    }

    const path = `/api/v1/conversations/${conversation.current}/messages`
    const events = client.stream<ChatStreamEvent>(path, { content: question }, options.signal)
    for await (const event of events) {
      const mapped = toStreamEvent(event)
      if (mapped !== null) {
        yield mapped
      }
    }
  }
}

/** `null` — событие, которому в ленте пока нет места. */
export function toStreamEvent(event: ChatStreamEvent): StreamEvent | null {
  switch (event.type) {
    case 'token':
      return { type: 'token', text: event.text }
    case 'tool_call':
      return { type: 'tool_call', toolCall: toToolCall(event) }
    case 'tool_result':
      return { type: 'tool_result', outcome: toToolOutcome(event) }
    case 'done':
      return { type: 'done' }
    case 'error':
      return { type: 'error', problem: event.problem }
    case 'citation':
      // Факт цитаты целиком: `toolCallId` делает маркер `[n]` кликабельным — он ведёт к
      // карточке Evidence того вызова `search_corpus`, который принёс цитату.
      return { type: 'citation', citation: toCitation(event) }
    default:
      // Неизвестный тип события — не повод рвать поток: контракт может обогнать клиент.
      return null
  }
}

/** Начатый вызов Инструмента — карточка Evidence в состоянии ожидания.
 *
 * Результата здесь ещё нет: событие приходит до выполнения. Аргументы становятся штампом
 * источника — по ним видно, за чем Агент пошёл, и по ним же запрос повторяется руками.
 */
function toToolCall(event: ToolCallEvent): ToolCall {
  const values = Object.values(event.arguments ?? {})
  return {
    id: event.tool_call_id,
    name: event.name,
    source: values.length === 0 ? 'no arguments' : values.map(display).join(' · '),
  }
}

/** Исход вызова: результат в форме §7.2 либо отказ Инструмента.
 *
 * Проекция приезжает готовой к показу — форматировать числа здесь нечем: какая метрика
 * процент, а какая счёт, знает бэкенд.
 */
function toToolOutcome(event: ToolResultEvent): ToolOutcome {
  return {
    id: event.tool_call_id,
    durationMs: event.duration_ms,
    ...(event.view != null && { result: toToolResult(event.view) }),
    ...(event.error != null && { error: { kind: event.error.kind, detail: event.error.detail } }),
  }
}

/** Проекция из контракта — она же модель карточки: формы `table`, `bars` и `quote`
 * совпадают полем в поле, и переписывать их значило бы копировать контракт руками.
 * `series` в контракте пока нет — его принесёт Инструмент сравнения. */
function toToolResult(view: EvidenceView): ToolResult {
  return view
}

/** Аргумент для штампа источника. Список записей (`compare_players`) читается как
 * «Stephen Curry 2015-16, Damian Lillard», а не как JSON. */
function display(value: unknown): string {
  if (typeof value === 'string') {
    return value
  }
  if (Array.isArray(value)) {
    return value.map(display).join(', ')
  }
  if (typeof value === 'object' && value !== null) {
    return Object.values(value)
      .filter((item) => item !== null)
      .map(display)
      .join(' ')
  }
  return JSON.stringify(value)
}
