import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'

import { formatWhen } from '@/features/chat/model/formatWhen'
import { fetchReportPlayers, fetchReportsPage } from '@/features/reports/api/reports'
import { ApiError, type Problem } from '@/shared/api/client'
import { useApiClient } from '@/shared/api/useApiClient'
import { Button } from '@/shared/ui/Button'
import { buttonClasses } from '@/shared/ui/buttonClasses'
import { EmptyState } from '@/shared/ui/EmptyState'
import { ErrorState } from '@/shared/ui/ErrorState'
import { Skeleton } from '@/shared/ui/Skeleton'

/** Список своих отчётов (Spine, §7.4): свежие сверху, фильтр по игроку живёт в search
 * params — ссылка на отфильтрованный список открывается так же. */
export function ReportsListPage({ playerId }: { playerId: number | undefined }) {
  const client = useApiClient()
  const navigate = useNavigate()

  const reports = useInfiniteQuery({
    queryKey: ['reports', 'list', playerId ?? null],
    queryFn: ({ pageParam }) =>
      fetchReportsPage(client, {
        cursor: pageParam,
        ...(playerId !== undefined && { playerId }),
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    retry: false,
  })
  const players = useQuery({
    queryKey: ['reports', 'players'],
    queryFn: () => fetchReportPlayers(client),
    retry: false,
  })

  const items = reports.data?.pages.flatMap((page) => page.items) ?? []

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex max-w-content flex-col gap-6 p-6">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="flex flex-col gap-1">
            <label htmlFor="reports-player" className="t-micro text-ink-muted">
              Player
            </label>
            <select
              id="reports-player"
              value={playerId === undefined ? '' : String(playerId)}
              onChange={(event) => {
                const value = event.target.value
                void navigate({
                  to: '/reports',
                  search: value === '' ? {} : { player: Number(value) },
                })
              }}
              className="t-body h-control-md min-w-48 border border-line bg-surface px-2 text-ink"
            >
              <option value="">All players</option>
              {players.data?.map((option) => (
                <option key={option.playerId} value={option.playerId}>
                  {option.fullName}
                </option>
              ))}
            </select>
          </div>
          <Link to="/reports/new" className={buttonClasses('solid')}>
            New report
          </Link>
        </div>

        {reports.isPending && <ListSkeleton />}

        {reports.isError && (
          <ErrorState
            problem={toProblem(reports.error)}
            onRetry={() => {
              void reports.refetch()
            }}
          />
        )}

        {reports.isSuccess && items.length === 0 && (
          <EmptyState
            title={
              playerId === undefined
                ? 'No scouting reports yet.'
                : 'No scouting reports for this player.'
            }
            action={
              <Link to="/reports/new" className={buttonClasses('solid')}>
                Generate report
              </Link>
            }
          />
        )}

        {items.length > 0 && (
          <ul className="flex flex-col border-l border-line">
            {items.map((item) => (
              <li key={item.report_id} className="border-b border-line">
                <Link
                  to="/reports/$reportId"
                  params={{ reportId: item.report_id }}
                  className="cv-transition-fast flex max-w-read flex-col gap-1 py-3 pl-4 hover:bg-fill"
                >
                  <span className="t-micro text-ink-muted">
                    {item.season} · {formatWhen(item.created_at)}
                  </span>
                  <span className="t-title text-ink">{item.player.full_name}</span>
                  <span className="t-body-sm text-ink-muted">{item.archetype}</span>
                  <span className="t-body line-clamp-2 text-ink-secondary">{item.summary}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}

        {reports.hasNextPage && (
          <Button
            disabled={reports.isFetchingNextPage}
            onClick={() => {
              void reports.fetchNextPage()
            }}
            className="w-fit"
          >
            {reports.isFetchingNextPage ? 'Loading…' : 'Load more'}
          </Button>
        )}
      </div>
    </div>
  )
}

function ListSkeleton() {
  return (
    <div role="status" aria-label="Loading reports" className="flex flex-col gap-6">
      {[0, 1, 2].map((key) => (
        <div key={key} className="flex flex-col gap-2">
          <Skeleton className="h-3 w-32" />
          <Skeleton className="h-5 w-[40%]" />
          <Skeleton className="h-4 w-full max-w-read" />
        </div>
      ))}
    </div>
  )
}

function toProblem(error: unknown): Problem {
  return error instanceof ApiError
    ? error.problem
    : {
        type: 'about:blank',
        title: 'Reports failed to load',
        status: 0,
        detail: 'Something went wrong while loading your reports. Retry to try again.',
        instance: '/api/v1/reports',
      }
}
