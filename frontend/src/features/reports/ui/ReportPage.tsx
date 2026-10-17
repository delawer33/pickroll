import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'

import { fetchReport } from '@/features/reports/api/reports'
import { ReportBody } from '@/features/reports/ui/ReportBody'
import { ApiError, type Problem } from '@/shared/api/client'
import { useApiClient } from '@/shared/api/useApiClient'
import { ErrorState } from '@/shared/ui/ErrorState'
import { Skeleton } from '@/shared/ui/Skeleton'

export function ReportPage({ reportId }: { reportId: string }) {
  const client = useApiClient()
  const report = useQuery({
    queryKey: ['report', reportId],
    queryFn: () => fetchReport(client, reportId),
    // Чужой или несуществующий отчёт — 404, повтор не поможет. Отчёты неизменны, так что
    // и перечитывать их незачем.
    retry: false,
    staleTime: Infinity,
  })

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex max-w-content flex-col gap-6 p-6">
        <BackLink />
        {report.isPending && <ReportSkeleton />}
        {report.isError && (
          <ErrorState
            problem={toProblem(report.error, reportId)}
            onRetry={() => {
              void report.refetch()
            }}
          />
        )}
        {report.isSuccess && (
          <article className="flex flex-col gap-8">
            <header className="flex flex-col gap-2">
              <p className="t-micro text-ink-muted">Scouting report · {report.data.season}</p>
              <h1 className="t-display text-ink">{report.data.player.full_name}</h1>
              <p className="t-title text-ink-secondary">{report.data.archetype}</p>
              <p className="t-mono border-t-2 border-ink border-b border-b-line py-2 text-ink-muted">
                {formatCreated(report.data.created_at)} · {report.data.model}
              </p>
            </header>
            <ReportBody report={report.data.content} />
          </article>
        )}
      </div>
    </div>
  )
}

export function BackLink() {
  return (
    <Link
      to="/reports"
      className="t-body-sm cv-transition-fast flex w-fit items-center gap-1 text-ink-secondary hover:text-ink"
    >
      <ArrowLeft aria-hidden="true" size={14} />
      All reports
    </Link>
  )
}

function ReportSkeleton() {
  return (
    <div role="status" aria-label="Loading report" className="flex flex-col gap-4">
      <Skeleton className="h-3 w-40" />
      <Skeleton className="h-9 w-[50%]" />
      <Skeleton className="h-4 w-full max-w-read" />
      <Skeleton className="h-4 w-[80%] max-w-read" />
      <Skeleton className="h-4 w-full max-w-read" />
    </div>
  )
}

function toProblem(error: unknown, reportId: string): Problem {
  return error instanceof ApiError
    ? error.problem
    : {
        type: 'about:blank',
        title: 'Report failed to load',
        status: 0,
        detail: 'Something went wrong while loading this report. Retry to try again.',
        instance: `/api/v1/reports/${reportId}`,
      }
}

function formatCreated(iso: string): string {
  const at = new Date(iso)
  return Number.isNaN(at.getTime()) ? iso : at.toISOString().slice(0, 10)
}
