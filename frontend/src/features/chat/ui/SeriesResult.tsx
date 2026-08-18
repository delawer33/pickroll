import { Suspense, lazy, useState } from 'react'

import type { Series } from '@/features/chat/model/types'
import { DataTable } from '@/shared/ui/DataTable'
import { Skeleton } from '@/shared/ui/Skeleton'
import { cn } from '@/shared/ui/cn'

// Recharts тянет за собой RTK и react-redux, поэтому грузится отдельным чанком и
// только когда Инструмент действительно вернул динамику (§6.1).
const SeriesChart = lazy(() =>
  import('@/features/chat/ui/SeriesChart').then((module) => ({
    default: module.SeriesChart,
  })),
)

export interface SeriesResultProps {
  metric: string
  unit: string
  series: Series[]
}

/** Динамика по сезонам с обязательным табличным представлением: часть слотов палитры
 * не добирает 3:1 к поверхности, и таблица — тот самый доступный путь к тем же числам,
 * без которого палитра нелегальна (§6.2). */
export function SeriesResult({ metric, unit, series }: SeriesResultProps) {
  const [view, setView] = useState<'chart' | 'table'>('chart')

  const columns = ['Season', ...series.map((line) => line.name)]
  const rows = (series[0]?.points ?? []).map((point, index) => [
    point.x,
    ...series.map((line) => {
      const value = line.points[index]
      return value === undefined ? '—' : String(value.y)
    }),
  ])

  return (
    <div className="grid gap-2">
      <div className="flex items-baseline gap-2">
        <p className="t-micro text-ink-muted">
          {metric} · {unit}
        </p>
        <div className="ml-auto flex border border-line" role="group" aria-label="View">
          <ViewButton
            active={view === 'chart'}
            onClick={() => {
              setView('chart')
            }}
          >
            Chart
          </ViewButton>
          <ViewButton
            active={view === 'table'}
            onClick={() => {
              setView('table')
            }}
          >
            Table
          </ViewButton>
        </div>
      </div>

      {view === 'chart' ? (
        <Suspense fallback={<Skeleton className="h-[200px] w-full" />}>
          <SeriesChart metric={metric} unit={unit} series={series} />
        </Suspense>
      ) : (
        <DataTable columns={columns} rows={rows} caption={`${metric}, ${unit}`} />
      )}
    </div>
  )
}

function ViewButton({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: string
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        'cv-transition-fast t-micro h-control-sm cursor-pointer px-2',
        active ? 'bg-fill-active text-ink' : 'text-ink-muted hover:bg-fill-hover',
      )}
    >
      {children}
    </button>
  )
}
