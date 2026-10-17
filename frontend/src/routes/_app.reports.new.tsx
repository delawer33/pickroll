import { createFileRoute } from '@tanstack/react-router'

import { NewReportPage } from '@/features/reports/ui/NewReportPage'

/** Новый отчёт: форма заказа и стриминг разделов. */
export const Route = createFileRoute('/_app/reports/new')({
  staticData: { title: 'New scouting report' },
  component: NewReportPage,
})
