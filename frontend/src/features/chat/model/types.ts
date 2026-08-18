import type { Problem } from '@/shared/api/client'

/** Модель Диалога на фронте — то, что рисуют компоненты.
 *
 * Это не контракт API: wire-типы генерируются из OpenAPI (`shared/api/wire.ts`) и руками
 * не пишутся. Здесь живёт модель ленты, которая отличается от контракта осознанно —
 * например, роль Агента называется `agent`, а не `assistant`, и Реплика знает, идёт ли
 * ещё её поток. Перевод одного в другое — в `features/chat/api/`.
 */

/** Одна серия динамики по сезонам: имя серии говорит, чьё это, точки — значения. */
export interface Series {
  name: string
  points: Array<{ x: string; y: number }>
}

/** Результат Инструмента — то, что показывается в карточке Evidence. */
export type ToolResult =
  | { kind: 'table'; columns: string[]; rows: string[][] }
  | { kind: 'bars'; bars: Array<{ label: string; percentile: number }> }
  | { kind: 'quote'; text: string }
  /** Динамика по сезонам: линия на сезон-ось, у каждой серии своё имя и слот палитры. */
  | { kind: 'series'; metric: string; unit: string; series: Series[] }

/** Вызов Инструмента со штампом источника и длительности: это провенанс, который
 * пользователь должен иметь возможность проверить (§7.2). */
export interface ToolCall {
  id: string
  name: string
  source: string
  /** Сколько занял вызов. Необязательно: в поток длительность не уезжает — событие
   * `tool_call` приходит до выполнения Инструмента, чтобы показать ожидание. */
  durationMs?: number
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

/** Источник потока ответа — единственный шов между лентой и транспортом.
 *
 * `signal` обязателен к соблюдению: брошенный ответ (ушли со страницы, задали новый вопрос)
 * должен закрыть запрос, а не висеть открытым до конца генерации на сервере.
 */
export type AnswerStream = (
  question: string,
  options?: { signal?: AbortSignal },
) => AsyncIterable<StreamEvent>
