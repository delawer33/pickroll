import { Suspense, lazy } from 'react'

import { playerHeading } from '@/features/chat/model/comparison'
import type { ComparisonCell, ToolResult } from '@/features/chat/model/types'
import { DataTable } from '@/shared/ui/DataTable'
import { Skeleton } from '@/shared/ui/Skeleton'

// Recharts тянет за собой RTK и react-redux, поэтому грузится отдельным чанком и только
// когда Инструмент действительно вернул сравнение (§6.1).
const ComparisonRadar = lazy(() =>
  import('@/features/chat/ui/ComparisonRadar').then((module) => ({
    default: module.ComparisonRadar,
  })),
)

type ComparisonProps = Omit<Extract<ToolResult, { kind: 'comparison' }>, 'kind'>

/** Сравнение игроков: side-by-side таблица и radar по Перцентилям.
 *
 * Таблица — основной путь к числам и обязательное табличное представление radar-чарта
 * (часть слотов палитры не добирает 3:1 к поверхности, §6.2). Ячейка — «значение
 * (Перцентиль)»; метрика, которой в сезоне не считали, — прочерк, а не ноль. */
export function ComparisonResult({ players, metrics }: ComparisonProps) {
  const columns = ['Metric', ...players.map(playerHeading)]
  const rows = metrics.map((metric) => [metric.label, ...metric.cells.map(cellText)])
  const unqualified = players.filter((player) => !player.qualified)

  return (
    <div className="grid gap-3">
      <DataTable columns={columns} rows={rows} caption="Player comparison" />
      <p className="t-micro text-ink-muted">
        value (percentile vs qualified players of the same season) · — not counted that season
      </p>
      {unqualified.length > 0 && (
        <p className="t-micro text-ink-muted">
          Not qualified (too few games or minutes), percentiles are unreliable:{' '}
          {unqualified.map((player) => `${player.name} ${player.season}`).join(', ')}
        </p>
      )}
      <Suspense fallback={<Skeleton className="h-[260px] w-full" />}>
        <ComparisonRadar players={players} metrics={metrics} />
      </Suspense>
    </div>
  )
}

function cellText(cell: ComparisonCell): string {
  // Без Перцентиля нечего ставить в скобки: прочерк целиком.
  return cell.percentile === null ? '—' : `${cell.value} (${String(cell.percentile)})`
}
