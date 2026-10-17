/** Чтение и заказ Скаутинг-отчётов через API. */

import type { ApiClient } from '@/shared/api/client'
import type {
  Page,
  ReportCreate,
  ReportResponse,
  ReportStreamEvent,
  ReportSummary,
} from '@/shared/api/wire'

const REPORTS = '/api/v1/reports'

/** Сколько отчётов в одной странице списка. */
export const REPORTS_PAGE_SIZE = 20

/** Потолок страницы на бэкенде: столько нужно, чтобы собрать игроков для фильтра. */
const MAX_PAGE_SIZE = 100

export function fetchReportsPage(
  client: ApiClient,
  options: { cursor?: string | null; playerId?: number } = {},
): Promise<Page<ReportSummary>> {
  const query = new URLSearchParams({ limit: String(REPORTS_PAGE_SIZE) })
  if (options.cursor != null) {
    query.set('cursor', options.cursor)
  }
  if (options.playerId !== undefined) {
    query.set('player_id', String(options.playerId))
  }
  return client.get<Page<ReportSummary>>(`${REPORTS}?${query.toString()}`)
}

export interface ReportPlayerOption {
  playerId: number
  fullName: string
}

/** Игроки, по которым у пользователя есть отчёты, — варианты фильтра списка.
 *
 * Отдельного эндпоинта «мои игроки» нет, поэтому берутся из первой страницы максимального
 * размера: фильтр предлагает игроков из свежих отчётов, а старше ста — см. docs/deferred.md. */
export async function fetchReportPlayers(client: ApiClient): Promise<ReportPlayerOption[]> {
  const page = await client.get<Page<ReportSummary>>(`${REPORTS}?limit=${MAX_PAGE_SIZE}`)
  const byId = new Map<number, ReportPlayerOption>()
  for (const item of page.items) {
    byId.set(item.player.player_id, {
      playerId: item.player.player_id,
      fullName: item.player.full_name,
    })
  }
  return [...byId.values()].sort((a, b) => a.fullName.localeCompare(b.fullName))
}

export function fetchReport(client: ApiClient, reportId: string): Promise<ReportResponse> {
  return client.get<ReportResponse>(`${REPORTS}/${reportId}`)
}

/** Заказ отчёта: SSE-поток снапшотов, затем `done` с идентификатором или `error`. */
export function streamReport(
  client: ApiClient,
  order: ReportCreate,
  signal?: AbortSignal,
): AsyncIterable<ReportStreamEvent> {
  return client.stream<ReportStreamEvent>(REPORTS, order, signal)
}
