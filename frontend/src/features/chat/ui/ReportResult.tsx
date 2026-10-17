import { Link } from '@tanstack/react-router'

import type { ReportItem } from '@/features/chat/model/types'

export interface ReportResultProps {
  items: ReportItem[]
}

/** Скаутинг-отчёты: анонс, а не сам отчёт. Карточка в чате показывает, чей отчёт и о чём он,
 * а полный разбор живёт на своей странице — туда ведёт ссылка (§7.2). */
export function ReportResult({ items }: ReportResultProps) {
  return (
    <ul className="grid gap-3">
      {items.map((item) => (
        <li key={item.report_id} className="grid min-w-0 gap-1">
          <p className="flex min-w-0 items-baseline gap-2">
            <span className="t-body-sm min-w-0 flex-1 truncate text-ink">{item.player}</span>
            <span className="t-mono shrink-0 text-ink-muted">{item.season}</span>
            {item.similarity != null && (
              <span className="num t-body-sm shrink-0 text-accent-text">
                {item.similarity.toFixed(3)}
              </span>
            )}
          </p>
          <p className="t-micro text-ink-muted">{item.archetype}</p>
          <p className="t-body-sm text-ink-secondary">{item.summary}</p>
          <Link
            to="/reports/$reportId"
            params={{ reportId: item.report_id }}
            className="t-body-sm cv-transition-fast w-fit text-accent-text underline underline-offset-2"
          >
            Open full report
          </Link>
        </li>
      ))}
    </ul>
  )
}
