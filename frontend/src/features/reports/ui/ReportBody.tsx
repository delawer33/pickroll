import type { ReactNode } from 'react'

import type { PartialScoutingReport } from '@/shared/api/wire'
import { Skeleton } from '@/shared/ui/Skeleton'

export interface ReportBodyProps {
  report: PartialScoutingReport
  /** Идёт генерация: разделы, которых ещё нет, стоят скелетонами в форме текста. Готовый
   * отчёт показывает только то, что в нём есть. */
  streaming?: boolean
}

/** Разделы Скаутинг-отчёта одной колонкой (Sheet, §7.1). Один и тот же компонент рисует
 * снапшот посреди генерации и сохранённый отчёт: раздел появляется, когда он есть. */
export function ReportBody({ report, streaming = false }: ReportBodyProps) {
  return (
    <div className="flex flex-col gap-8">
      <Prose title="Summary" text={report.summary} streaming={streaming} />
      <Points title="Strengths" points={report.strengths} streaming={streaming} />
      <Points title="Weaknesses" points={report.weaknesses} streaming={streaming} />
      <Prose title="Statistical profile" text={report.stat_profile} streaming={streaming} />
      <Comparables comparables={report.comparables} streaming={streaming} />
      <Prose title="Outlook" text={report.outlook} streaming={streaming} />
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex max-w-read flex-col gap-3">
      <h2 className="t-micro border-t-2 border-ink pt-2 text-ink">{title}</h2>
      {children}
    </section>
  )
}

function Pending() {
  return (
    <div role="status" aria-label="Writing section" className="flex flex-col gap-2">
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-[85%]" />
    </div>
  )
}

function Prose({
  title,
  text,
  streaming,
}: {
  title: string
  text: string | null | undefined
  streaming: boolean
}) {
  if (text == null) {
    return streaming ? (
      <Section title={title}>
        <Pending />
      </Section>
    ) : null
  }
  return (
    <Section title={title}>
      <p className="t-read text-ink">{text}</p>
    </Section>
  )
}

function Points({
  title,
  points,
  streaming,
}: {
  title: string
  points: PartialScoutingReport['strengths']
  streaming: boolean
}) {
  if (points == null) {
    return streaming ? (
      <Section title={title}>
        <Pending />
      </Section>
    ) : null
  }
  return (
    <Section title={title}>
      <ul className="flex flex-col">
        {points.map((point, index) => (
          <li key={index} className="flex flex-col gap-1 border-b border-line py-2 first:pt-0">
            <p className="t-read text-ink">{point.claim}</p>
            {(point.metrics?.length ?? 0) > 0 && (
              <p className="t-mono flex flex-wrap gap-x-3 text-ink-muted">
                {point.metrics?.map((metric) => (
                  <span key={metric}>{metric}</span>
                ))}
              </p>
            )}
          </li>
        ))}
      </ul>
    </Section>
  )
}

function Comparables({
  comparables,
  streaming,
}: {
  comparables: PartialScoutingReport['comparables']
  streaming: boolean
}) {
  if (comparables == null) {
    return streaming ? (
      <Section title="Similar players">
        <Pending />
      </Section>
    ) : null
  }
  return (
    <Section title="Similar players">
      <ul className="flex flex-col">
        {comparables.map((comparable, index) => (
          <li key={index} className="flex flex-col gap-1 border-b border-line py-2 first:pt-0">
            <p className="flex items-baseline gap-2">
              <span className="t-heading text-ink">{comparable.player_name}</span>
              <span className="t-mono text-ink-muted">{comparable.season}</span>
            </p>
            <p className="t-body text-ink-secondary">{comparable.reason}</p>
          </li>
        ))}
      </ul>
    </Section>
  )
}
