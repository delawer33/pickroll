import {
  Legend,
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
} from 'recharts'

import { playerHeading } from '@/features/chat/model/comparison'
import type { ComparisonMetric, ComparisonPlayer } from '@/features/chat/model/types'
import { usePrefersReducedMotion } from '@/shared/ui/usePrefersReducedMotion'

export interface ComparisonRadarProps {
  players: ComparisonPlayer[]
  metrics: ComparisonMetric[]
}

/** Слоты категориальной палитры назначаются по порядку (§6.3); сравнение — до четырёх. */
const SERIES_COLOR = [
  'var(--cv-series-1)',
  'var(--cv-series-2)',
  'var(--cv-series-3)',
  'var(--cv-series-4)',
]

const AXIS_TICK = {
  fill: 'var(--cv-ink-muted)',
  fontSize: 10,
  letterSpacing: '0.1em',
} as const

/** Radar по Перцентилям: ось — метрика, радиус — Перцентиль 0–100.
 *
 * Ось, на которой ни у кого нет значения (метрика не считалась ни в одном из сезонов),
 * не рисуется. Если же не считалась у кого-то одного, его точка стоит в центре, а тултип
 * говорит «—»: форма полигона для такого игрока неполна, и честное объяснение — в таблице
 * рядом, где там стоит прочерк, а не ноль. */
export function ComparisonRadar({ players, metrics }: ComparisonRadarProps) {
  const reducedMotion = usePrefersReducedMotion()

  const axes = metrics.filter((metric) => metric.cells.some((cell) => cell.percentile !== null))
  // Полигон из двух осей — отрезок: читать нечего, таблица говорит то же самое.
  if (axes.length < 3) {
    return null
  }

  const rows = axes.map((metric) => {
    const row: Record<string, string | number | null> = { label: metric.label }
    metric.cells.forEach((cell, index) => {
      row[`p${String(index)}`] = cell.percentile ?? 0
      row[`missing${String(index)}`] = cell.percentile === null ? 1 : 0
    })
    return row
  })

  return (
    <div className="h-[280px] w-full" role="img" aria-label="Radar chart of percentiles">
      <ResponsiveContainer width="100%" height="100%">
        <RadarChart data={rows} outerRadius="70%">
          <PolarGrid stroke="var(--cv-line)" />
          <PolarAngleAxis dataKey="label" tick={AXIS_TICK} />
          <PolarRadiusAxis domain={[0, 100]} tick={false} axisLine={false} />
          <Tooltip content={<RadarTooltip players={players} />} />
          <Legend
            verticalAlign="bottom"
            height={24}
            formatter={(value: string) => (
              <span className="t-body-sm text-ink-secondary">{value}</span>
            )}
          />
          {players.map((player, index) => (
            <Radar
              key={playerHeading(player) + String(index)}
              name={playerHeading(player)}
              dataKey={`p${String(index)}`}
              stroke={SERIES_COLOR[index] ?? 'var(--cv-ink-muted)'}
              fill={SERIES_COLOR[index] ?? 'var(--cv-ink-muted)'}
              fillOpacity={0.12}
              strokeWidth={2}
              isAnimationActive={!reducedMotion}
            />
          ))}
        </RadarChart>
      </ResponsiveContainer>
    </div>
  )
}

interface RadarTooltipProps {
  players: ComparisonPlayer[]
  /** Дополняется библиотекой в момент ховера. */
  active?: boolean
  label?: unknown
  payload?: Array<{
    name?: string | undefined
    value?: number | undefined
    color?: string | undefined
    dataKey?: unknown
    payload?: Record<string, unknown> | undefined
  }>
}

/** Свой тултип, чтобы носить наши поверхности и шрифты (§6.1). Идентичность серии несёт
 * цветная метка рядом — цвет никогда не единственный носитель смысла. */
function RadarTooltip({ active, payload, label }: RadarTooltipProps) {
  if (active !== true || payload === undefined || payload.length === 0) {
    return null
  }

  return (
    <div className="border border-line bg-overlay p-2 shadow-overlay">
      <p className="t-micro text-ink-muted">{String(label)} · percentile</p>
      <ul className="mt-1 grid gap-1">
        {payload.map((entry) => {
          const index = String(entry.dataKey).slice(1)
          const missing = entry.payload?.[`missing${index}`] === 1
          return (
            <li key={entry.name} className="t-body-sm flex items-center gap-2 text-ink">
              <span
                aria-hidden="true"
                className="h-2 w-2 shrink-0"
                style={{ background: entry.color }}
              />
              <span className="text-ink-secondary">{entry.name}</span>
              <span className="num ml-auto">{missing ? '—' : entry.value}</span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
