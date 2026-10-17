/** Типы контракта API — узкие псевдонимы к сгенерированной схеме.
 *
 * `schema.d.ts` собирается из OpenAPI бэкенда (`pnpm api:types`) и руками не правится.
 * Фичи импортируют отсюда, а не лазят в `components['schemas'][...]`: тогда переезд
 * генератора или переименование модели на бэкенде правится в одном файле.
 */

import type { components } from '@/shared/api/schema'

type Schemas = components['schemas']

/** Тело ошибки RFC 9457; расширяющие члены (`candidates`, `cause`, ...) — по ключу. */
export type Problem = Schemas['Problem']

export type ConversationSummary = Schemas['ConversationSummary']
export type MessageResponse = Schemas['MessageResponse']

/** Событие SSE-потока ответа. Разбирается по полю `type` — исчерпывающе, потому что это
 * дискриминированное объединение из OpenAPI. */
export type ChatStreamEvent = Schemas['ChatStreamEvent']

export type TokenEvent = Schemas['TokenEvent']
export type ToolCallEvent = Schemas['ToolCallEvent']
export type ToolResultEvent = Schemas['ToolResultEvent']
/** Результат Инструмента в форме карточки Evidence: таблица, полосы или цитата (§7.2). */
export type EvidenceView = Schemas['EvidenceView']
export type CitationEvent = Schemas['CitationEvent']
export type DoneEvent = Schemas['DoneEvent']
export type ErrorEvent = Schemas['ErrorEvent']

export type ReportSummary = Schemas['ReportSummary']
export type ReportResponse = Schemas['ReportResponse']
export type ReportCreate = Schemas['ReportCreate']
/** Снапшот отчёта по ходу генерации: каждое поле появляется, когда модель дописала раздел. */
export type PartialScoutingReport = Schemas['PartialScoutingReport']
/** Событие SSE-потока `POST /reports`: `snapshot`, затем `done` либо `error`. */
export type ReportStreamEvent = Schemas['ReportStreamEvent']

/** Страница cursor-пагинации. В OpenAPI это отдельная модель на каждый тип элемента
 * (`Page_ConversationSummary_`), а по смыслу — одна форма. */
export interface Page<ItemT> {
  items: ItemT[]
  next_cursor: string | null
}
