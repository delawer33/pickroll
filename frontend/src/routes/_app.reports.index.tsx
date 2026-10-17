import { createFileRoute } from '@tanstack/react-router'

import { ReportsListPage } from '@/features/reports/ui/ReportsListPage'

/** Список Скаутинг-отчётов. Фильтр по игроку — в search params: он переживает F5 и
 * шарится ссылкой (docs/design-system.md §8). */
export const Route = createFileRoute('/_app/reports/')({
  staticData: { title: 'Scouting reports' },
  validateSearch: (search: Record<string, unknown>): { player?: number } => {
    const player = Number(search.player)
    return Number.isInteger(player) && player > 0 ? { player } : {}
  },
  component: ReportsRoute,
})

function ReportsRoute() {
  const { player } = Route.useSearch()
  return <ReportsListPage playerId={player} />
}
