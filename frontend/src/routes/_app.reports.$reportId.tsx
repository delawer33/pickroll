import { createFileRoute } from '@tanstack/react-router'

import { ReportPage } from '@/features/reports/ui/ReportPage'

/** Сохранённый Скаутинг-отчёт: живёт по своему адресу и открывается после F5. */
export const Route = createFileRoute('/_app/reports/$reportId')({
  staticData: { title: 'Scouting report' },
  component: ReportRoute,
})

function ReportRoute() {
  const { reportId } = Route.useParams()
  return <ReportPage reportId={reportId} />
}
