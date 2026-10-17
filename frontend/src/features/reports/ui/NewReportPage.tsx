import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useCallback, useState, type FormEvent } from 'react'

import { useReportGeneration } from '@/features/reports/model/useReportGeneration'
import { BackLink } from '@/features/reports/ui/ReportPage'
import { ReportBody } from '@/features/reports/ui/ReportBody'
import { useApiClient } from '@/shared/api/useApiClient'
import { Button } from '@/shared/ui/Button'
import { ErrorState } from '@/shared/ui/ErrorState'

/** Формат сезона, как у бэкенда (`SEASON_PATTERN`): `2023-24`. */
const SEASON = /^\d{4}-\d{2}$/

/** Форма заказа и ход генерации на одной странице: разделы дописываются под формой по
 * мере прихода снапшотов, по `done` страница уходит на сохранённый отчёт. */
export function NewReportPage() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const [player, setPlayer] = useState('')
  const [season, setSeason] = useState('')

  const onDone = useCallback(
    (reportId: string) => {
      // Список обязан показать новый отчёт, не дожидаясь перезагрузки.
      void queryClient.invalidateQueries({ queryKey: ['reports'] })
      void navigate({ to: '/reports/$reportId', params: { reportId } })
    },
    [queryClient, navigate],
  )
  const { state, start, retry } = useReportGeneration({ client, onDone })

  const trimmedSeason = season.trim()
  const seasonInvalid = trimmedSeason !== '' && !SEASON.test(trimmedSeason)
  const streaming = state.status === 'streaming'
  const canSubmit = player.trim() !== '' && !seasonInvalid && !streaming

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!canSubmit) {
      return
    }
    start({
      player_name: player.trim(),
      season: trimmedSeason === '' ? null : trimmedSeason,
      language: 'en',
    })
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex max-w-content flex-col gap-8 p-6">
        <BackLink />

        <form onSubmit={submit} className="flex max-w-read flex-col gap-4">
          <div className="flex flex-col gap-1">
            <label htmlFor="report-player" className="t-micro text-ink-muted">
              Player
            </label>
            <input
              id="report-player"
              value={player}
              onChange={(event) => {
                setPlayer(event.target.value)
              }}
              placeholder="Jalen Brunson"
              autoComplete="off"
              disabled={streaming}
              className="t-body h-control-md border border-line bg-surface px-3 text-ink placeholder:text-ink-muted"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="report-season" className="t-micro text-ink-muted">
              Season (optional)
            </label>
            <input
              id="report-season"
              value={season}
              onChange={(event) => {
                setSeason(event.target.value)
              }}
              placeholder="2023-24"
              autoComplete="off"
              disabled={streaming}
              aria-invalid={seasonInvalid}
              aria-describedby="report-season-hint"
              className="t-body h-control-md w-40 border border-line bg-surface px-3 text-ink placeholder:text-ink-muted aria-[invalid=true]:border-critical"
            />
            <p
              id="report-season-hint"
              className={seasonInvalid ? 't-body-sm text-critical' : 't-body-sm text-ink-muted'}
            >
              {seasonInvalid
                ? 'Use the format 2023-24.'
                : 'Leave empty for the latest season of the player.'}
            </p>
          </div>
          <Button type="submit" variant="solid" disabled={!canSubmit} className="w-fit">
            {streaming ? 'Generating…' : 'Generate report'}
          </Button>
        </form>

        {state.status === 'error' && (
          <ErrorState problem={state.problem} onRetry={retry} className="max-w-read" />
        )}

        {state.status === 'streaming' && (
          <div aria-live="polite" aria-label="Report in progress">
            <ReportBody report={state.snapshot} streaming />
          </div>
        )}
      </div>
    </div>
  )
}
