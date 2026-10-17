import { useCallback, useEffect, useRef, useState } from 'react'

import { streamReport } from '@/features/reports/api/reports'
import { ApiError, type ApiClient, type Problem } from '@/shared/api/client'
import type { PartialScoutingReport, ReportCreate } from '@/shared/api/wire'

export type ReportGeneration =
  | { status: 'idle' }
  /** Поток идёт: `snapshot` — последний пришедший снапшот, он целиком заменяет прежний. */
  | { status: 'streaming'; order: ReportCreate; snapshot: PartialScoutingReport }
  /** Отчёт не получился и не сохранён — повтор безопасен. */
  | { status: 'error'; order: ReportCreate; problem: Problem }

export interface UseReportGenerationOptions {
  client: ApiClient
  /** Отчёт сохранён: страница уходит на него. */
  onDone: (reportId: string) => void
}

export interface UseReportGeneration {
  state: ReportGeneration
  start: (order: ReportCreate) => void
  /** Тот же заказ ещё раз; осмысленно только из состояния `error`. */
  retry: () => void
}

const DROPPED: Problem = {
  type: 'urn:court-vision:problem:stream-dropped',
  title: 'Report stream interrupted',
  status: 0,
  detail: 'The connection dropped before the report was saved. Nothing was stored; retry.',
  instance: '/api/v1/reports',
}

/** Заказ отчёта потоком: разделы приходят снапшотами, `done` отдаёт идентификатор.
 *
 * Состояние хука — не «отчёт», а ход генерации: сохранённый отчёт живёт на своей
 * странице и читается обычным запросом, поэтому по `done` мы не держим результат, а
 * уходим к нему. Брошенный поток (уход со страницы, повтор) отменяется — генерация
 * стоит денег, и ждать её ради закрытой вкладки незачем. */
export function useReportGeneration({
  client,
  onDone,
}: UseReportGenerationOptions): UseReportGeneration {
  const [state, setState] = useState<ReportGeneration>({ status: 'idle' })
  const controller = useRef<AbortController | null>(null)
  // Свежий колбэк без пересоздания `start`: иначе смена идентичности `onDone` на ререндере
  // пересоздала бы запуск, а идущий поток читал бы устаревшее замыкание.
  const doneRef = useRef(onDone)
  useEffect(() => {
    doneRef.current = onDone
  }, [onDone])

  useEffect(
    () => () => {
      controller.current?.abort()
    },
    [],
  )

  const start = useCallback(
    (order: ReportCreate) => {
      controller.current?.abort()
      const run = new AbortController()
      controller.current = run
      setState({ status: 'streaming', order, snapshot: {} })

      void (async () => {
        try {
          for await (const event of streamReport(client, order, run.signal)) {
            if (run.signal.aborted) {
              return
            }
            if (event.type === 'snapshot') {
              setState({ status: 'streaming', order, snapshot: event.report })
            } else if (event.type === 'done') {
              doneRef.current(event.report_id)
              return
            } else {
              setState({ status: 'error', order, problem: event.problem })
              return
            }
          }
          // Поток закрылся без `done` и без `error`: отчёт не сохранён.
          if (!run.signal.aborted) {
            setState({ status: 'error', order, problem: DROPPED })
          }
        } catch (cause) {
          if (run.signal.aborted) {
            return
          }
          setState({
            status: 'error',
            order,
            problem: cause instanceof ApiError ? cause.problem : DROPPED,
          })
        }
      })()
    },
    [client],
  )

  const retry = useCallback(() => {
    if (state.status === 'error') {
      start(state.order)
    }
  }, [state, start])

  return { state, start, retry }
}
