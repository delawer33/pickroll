import type { Problem } from '@/shared/api/client'

/** Модель Диалога на фронте.
 *
 * Формы событий повторяют контракт SSE из среза #8 (`token`, `tool_call`, `citation`,
 * `done`, `error`): когда приедет настоящий эндпоинт, типы будут сгенерированы из
 * OpenAPI через `openapi-typescript`, а этот файл — удалён. До тех пор он держит ровно
 * ту же форму, чтобы подмена источника не переписывала компоненты.
 */

/** Результат Инструмента — то, что показывается в карточке Evidence. */
export type ToolResult =
  | { kind: 'table'; columns: string[]; rows: string[][] }
  | { kind: 'bars'; bars: Array<{ label: string; percentile: number }> }
  | { kind: 'quote'; text: string }

/** Вызов Инструмента со штампом источника и длительности: это провенанс, который
 * пользователь должен иметь возможность проверить (§7.2). */
export interface ToolCall {
  id: string
  name: string
  source: string
  durationMs: number
  result: ToolResult
}

/** Цитата: надстрочный номер в ответе, указывающий на карточку Evidence. */
export interface Citation {
  index: number
  toolCallId: string
}

export interface UserMessage {
  id: string
  role: 'user'
  text: string
}

export interface AgentMessage {
  id: string
  role: 'agent'
  text: string
  citations: Citation[]
  /** Поток ещё идёт: под текстом стоит курсор печати. */
  streaming: boolean
  /** Поток закончился ошибкой провайдера — ответ остаётся, ошибка показывается рядом. */
  error?: Problem
}

export type Message = UserMessage | AgentMessage

export type StreamEvent =
  | { type: 'token'; text: string }
  | { type: 'tool_call'; toolCall: ToolCall }
  | { type: 'citation'; citation: Citation }
  | { type: 'done' }
  | { type: 'error'; problem: Problem }

/** Источник потока ответа. Единственный шов между UI и транспортом: в этом срезе за
 * ним стоит мок, в #8 — SSE. */
export type AnswerStream = (question: string) => AsyncIterable<StreamEvent>
