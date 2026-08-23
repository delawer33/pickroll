/** Настоящий поток ответа Агента — на месте мока из #6.
 *
 * Шов остался тот же (`AnswerStream`), поменялось только то, что за ним стоит: события
 * приходят из SSE и переводятся в модель ленты. Компоненты этой замены не заметили.
 */

import { createConversation } from '@/features/chat/api/conversations'
import type { AnswerStream, StreamEvent, ToolCall } from '@/features/chat/model/types'
import type { ApiClient } from '@/shared/api/client'
import type { ChatStreamEvent, ToolCallEvent } from '@/shared/api/wire'

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
    case 'done':
      return { type: 'done' }
    case 'error':
      return { type: 'error', problem: event.problem }
    case 'citation':
      // Цитаты объявлены в контракте, но до спринта 2 их никто не присылает, и связать их
      // с карточкой Evidence пока не с чем: у ленты цитата указывает на вызов Инструмента,
      // а у корпуса — на чанк документа. Обе половины приезжают вместе с RAG.
      return null
    default:
      // Неизвестный тип события — не повод рвать поток: контракт может обогнать клиент.
      return null
  }
}

/** Вызов Инструмента как карточка Evidence.
 *
 * Результат Инструмента в поток не уезжает — он бывает большим, а ответ Агента и без него
 * опирается на цифры. Поэтому карточка показывает то, что известно: чем и с какими
 * аргументами Агент пошёл за данными. Провенанс от этого не страдает: аргументы — ровно
 * то, что нужно, чтобы повторить запрос руками.
 */
function toToolCall(event: ToolCallEvent): ToolCall {
  const entries = Object.entries(event.arguments ?? {})
  return {
    id: event.tool_call_id,
    name: event.name,
    source:
      entries.length === 0
        ? 'no arguments'
        : entries.map(([, value]) => display(value)).join(' · '),
    result: {
      kind: 'table',
      columns: ['Argument', 'Value'],
      rows: entries.map(([key, value]) => [key, display(value)]),
    },
  }
}

function display(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value)
}
