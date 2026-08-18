import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import type { Series } from '@/features/chat/model/types'
import { usePrefersReducedMotion } from '@/shared/ui/usePrefersReducedMotion'

export interface SeriesChartProps {
  metric: string
  unit: string
  series: Series[]
}

/** Слоты категориальной палитры назначаются по порядку и никогда не цикличны: девятая
 * серия — это «Прочие» (§6.3). Цвет берётся из токена: CSS-переменные работают в
 * SVG-атрибутах, а hex внутри компонента графика — ошибка ревью (§6.1). */
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
  fontVariantNumeric: 'tabular-nums',
} as const

/** Динамика по сезонам — change-over-time, поэтому линия: 2px, маркеры ≥8px (§6.5).
 * Ховер остаётся и под `prefers-reduced-motion` — он обратная связь, а не украшение;
 * выключается только анимация перерисовки (§6.2). */
export function SeriesChart({ metric, unit, series }: SeriesChartProps) {
  const reducedMotion = usePrefersReducedMotion()

  // Recharts ждёт строку на точку X, а не серию на массив.
  const rows = (series[0]?.points ?? []).map((point, index) => {
    const row: Record<string, string | number> = { x: point.x }
    for (const line of series) {
      const value = line.points[index]
      if (value !== undefined) {
        row[line.name] = value.y
      }
    }
    return row
  })

  return (
    <div className="h-[200px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
          <CartesianGrid stroke="var(--cv-line-row)" vertical={false} />
          <XAxis
            dataKey="x"
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={{ stroke: 'var(--cv-line)' }}
          />
          <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={44} />
          {/* Тултип передаётся элементом: Recharts дополняет его props данными точки
              под курсором. */}
          <Tooltip
            cursor={{ stroke: 'var(--cv-line)' }}
            content={<ChartTooltip metric={metric} unit={unit} />}
          />
          {/* Легенда обязательна при двух и более сериях; при одной её имя несёт
              заголовок карточки (§6.2). */}
          {series.length > 1 && (
            <Legend
              verticalAlign="bottom"
              height={24}
              formatter={(value: string) => (
                <span className="t-body-sm text-ink-secondary">{value}</span>
              )}
            />
          )}
          {series.map((line, index) => (
            <Line
              key={line.name}
              type="linear"
              dataKey={line.name}
              stroke={SERIES_COLOR[index] ?? 'var(--cv-ink-muted)'}
              strokeWidth={2}
              dot={{
                r: 4,
                strokeWidth: 0,
                fill: SERIES_COLOR[index] ?? 'var(--cv-ink-muted)',
              }}
              activeDot={{ r: 5 }}
              isAnimationActive={!reducedMotion}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

interface ChartTooltipProps {
  metric: string
  unit: string
  /** Дополняется библиотекой в момент ховера. */
  active?: boolean
  label?: unknown
  payload?: Array<{
    name?: string | undefined
    value?: number | undefined
    color?: string | undefined
  }>
}

/** Свой тултип, чтобы носить наши поверхности, тени и шрифты (§6.1). Значения набраны
 * текстовыми токенами, идентичность серии несёт цветная метка рядом — цвет никогда не
 * единственный носитель смысла. */
function ChartTooltip({ active, payload, label, metric, unit }: ChartTooltipProps) {
  if (active !== true || payload === undefined || payload.length === 0) {
    return null
  }

  return (
    <div className="border border-line bg-overlay p-2 shadow-overlay">
      <p className="t-micro text-ink-muted">
        {String(label)} · {metric}
      </p>
      <ul className="mt-1 grid gap-1">
        {payload.map((entry) => (
          <li key={entry.name} className="t-body-sm flex items-center gap-2 text-ink">
            <span
              aria-hidden="true"
              className="h-2 w-2 shrink-0"
              style={{ background: entry.color }}
            />
            <span className="text-ink-secondary">{entry.name}</span>
            <span className="num ml-auto">
              {entry.value} {unit}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
