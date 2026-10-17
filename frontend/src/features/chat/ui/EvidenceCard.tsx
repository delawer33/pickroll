import { AlertTriangle } from 'lucide-react'

import type { ToolCall, ToolResult } from '@/features/chat/model/types'
import { ComparisonResult } from '@/features/chat/ui/ComparisonResult'
import { SeriesResult } from '@/features/chat/ui/SeriesResult'
import { Collapsible } from '@/shared/ui/Collapsible'
import { DataTable } from '@/shared/ui/DataTable'
import { cn } from '@/shared/ui/cn'

export interface EvidenceCardProps {
  toolCall: ToolCall
  /** Карточка, на которую указала цитата: подсвечивается акцентной рамкой. */
  active?: boolean
}

/** Карточка Evidence — главная витрина продукта: она показывает, откуда взято число.
 * Поэтому получает собственный вес (линейка вокруг и поверхность листа), а не серый
 * мелкий текст под ответом (§7.2). */
export function EvidenceCard({ toolCall, active = false }: EvidenceCardProps) {
  return (
    <article
      id={`evidence-${toolCall.id}`}
      data-active={active}
      className={cn(
        'cv-transition-fast cv-enter grid min-w-0 gap-2 border bg-fill p-3',
        active ? 'border-accent' : 'border-line',
      )}
    >
      <Collapsible
        summary={
          <header className="t-mono flex w-full min-w-0 items-baseline gap-2 text-ink-muted">
            <span className="shrink-0 text-ink">{toolCall.name}</span>
            {/* Штамп источника — первое, чем можно пожертвовать по ширине: имя
                Инструмента и длительность нужны целиком. */}
            <span className="min-w-0 flex-1 truncate">{toolCall.source}</span>
            {/* Длительности нет у вызова, о котором сообщили до его выполнения — пустое
                место лучше, чем «0 ms», читаемое как мгновенный вызов. */}
            {toolCall.durationMs !== undefined && (
              <span className="num shrink-0">{toolCall.durationMs} ms</span>
            )}
          </header>
        }
      >
        <div className="pt-2">
          <ToolOutcomeView toolCall={toolCall} />
        </div>
      </Collapsible>
    </article>
  )
}

/** Три состояния вызова: идёт, вернул данные, вернул отказ.
 *
 * Отказ показывается наравне с данными и подписан причиной — иначе вызов, на котором
 * данных не нашлось, выглядит в рейке ровно как удачный, и «Агент сходил за данными»
 * читается там, где Агент вернулся ни с чем. */
function ToolOutcomeView({ toolCall }: { toolCall: ToolCall }) {
  if (toolCall.error !== undefined) {
    return (
      <div className="flex gap-2" role="status">
        <AlertTriangle aria-hidden="true" size={14} className="mt-[2px] shrink-0 text-critical" />
        <div className="min-w-0">
          <p className="t-mono text-critical">{toolCall.error.kind}</p>
          <p className="t-body-sm text-ink-secondary">{toolCall.error.detail}</p>
        </div>
      </div>
    )
  }

  // Ожидание — это состояние, а не пустота: карточка уже стоит в рейке, потому что
  // событие о вызове приходит до его выполнения. Признак — отсутствие длительности, а не
  // отсутствие результата: Инструмент мог отработать и не дать проекции, и такой вызов
  // ожиданием называть нельзя — он уже кончился.
  if (toolCall.durationMs === undefined) {
    return (
      <p className="t-body-sm text-ink-muted" role="status">
        Running…
      </p>
    )
  }

  if (toolCall.result === undefined) {
    return <p className="t-body-sm text-ink-muted">Nothing to show for this result.</p>
  }

  return <ToolResultView result={toolCall.result} />
}

function ToolResultView({ result }: { result: ToolResult }) {
  if (result.kind === 'quote') {
    return (
      <blockquote className="t-body-sm border-l-2 border-line pl-3 text-ink-secondary">
        {result.text}
      </blockquote>
    )
  }

  if (result.kind === 'quotes') {
    // Выдача поиска по корпусу: у каждой цитаты номер `[n]` — тот же, что у маркера в
    // ответе, — и mono-локатор документа. Это и делает карточку провенансом (§7.2).
    return (
      <ul className="grid gap-3">
        {result.quotes.map((quote) => (
          <li key={quote.index} className="grid min-w-0 gap-1">
            <p className="t-mono flex min-w-0 items-baseline gap-2 text-ink-muted">
              <span className="num shrink-0 text-accent-text">[{quote.index}]</span>
              <span className="min-w-0 flex-1 truncate">
                {quote.title} · {quote.locator}
              </span>
            </p>
            <blockquote className="t-body-sm border-l-2 border-line pl-3 text-ink-secondary">
              {quote.text}
            </blockquote>
          </li>
        ))}
      </ul>
    )
  }

  if (result.kind === 'table') {
    return <DataTable columns={result.columns} rows={result.rows} />
  }

  if (result.kind === 'comparison') {
    return <ComparisonResult players={result.players} metrics={result.metrics} />
  }

  if (result.kind === 'series') {
    return <SeriesResult metric={result.metric} unit={result.unit} series={result.series} />
  }

  return (
    <div className="grid gap-2">
      {result.caption != null && <p className="t-micro text-ink-muted">{result.caption}</p>}
      {result.bars.map((bar) => (
        <div
          key={bar.label}
          className="grid grid-cols-[84px_minmax(0,1fr)_28px] items-center gap-3"
        >
          <span className="t-micro text-right text-ink-muted">{bar.label}</span>
          {/* Перцентиль — полярность относительно 50-го, а не магнитуда: полоса
              растёт от середины в свою сторону (§6.5). */}
          <span className="relative block h-[10px] bg-fill">
            <span
              aria-hidden="true"
              className="absolute top-0 bottom-0 -mx-px w-px bg-line left-1/2"
            />
            {bar.percentile !== null && (
              <span
                aria-hidden="true"
                className={cn(
                  'absolute top-0 bottom-0',
                  bar.percentile >= 50 ? 'left-1/2 bg-div-pos' : 'right-1/2 bg-div-neg',
                )}
                style={{ width: `${String(Math.abs(bar.percentile - 50))}%` }}
              />
            )}
          </span>
          <span className="num t-body-sm text-right text-ink-secondary">
            {bar.percentile ?? '—'}
          </span>
        </div>
      ))}
    </div>
  )
}
