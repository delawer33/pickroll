import type { ToolCall, ToolResult } from '@/features/chat/model/types'
import { cn } from '@/shared/ui/cn'
import { Collapsible } from '@/shared/ui/Collapsible'

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
            <span className="num shrink-0">{toolCall.durationMs} ms</span>
          </header>
        }
      >
        <div className="pt-2">
          <ToolResultView result={toolCall.result} />
        </div>
      </Collapsible>
    </article>
  )
}

function ToolResultView({ result }: { result: ToolResult }) {
  if (result.kind === 'quote') {
    return (
      <blockquote className="t-body-sm border-l-2 border-line pl-3 text-ink-secondary">
        {result.text}
      </blockquote>
    )
  }

  if (result.kind === 'table') {
    return (
      // Широкий контент скроллится внутри себя: страница по горизонтали не
      // скроллится никогда (§7.3).
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              {result.columns.map((column) => (
                <th
                  key={column}
                  scope="col"
                  className="t-micro border-b-2 border-r border-b-line-heavy border-r-line-row px-2 pt-2 pb-1 text-right whitespace-nowrap text-ink-muted last:border-r-0 first:text-left"
                >
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.map((row, rowIndex) => (
              // Ключ по позиции: строки таблицы приходят как есть и могут повторяться
              // значениями (два сезона с одинаковым сплитом).
              <tr key={rowIndex}>
                {row.map((cell, column) => (
                  <td
                    key={column}
                    className="num h-row-compact border-r border-b border-r-line-row border-b-line-row px-2 text-right whitespace-nowrap text-ink-secondary last:border-r-0 first:text-left first:font-medium first:text-ink"
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  return (
    <div className="grid gap-2">
      {result.bars.map((bar) => (
        <div key={bar.label} className="grid grid-cols-[84px_minmax(0,1fr)_28px] items-center gap-3">
          <span className="t-micro text-right text-ink-muted">{bar.label}</span>
          {/* Перцентиль — полярность относительно 50-го, а не магнитуда: полоса
              растёт от середины в свою сторону (§6.5). */}
          <span className="relative block h-[10px] bg-fill">
            <span
              aria-hidden="true"
              className="absolute top-0 bottom-0 -mx-px w-px bg-line left-1/2"
            />
            <span
              aria-hidden="true"
              className={cn(
                'absolute top-0 bottom-0',
                bar.percentile >= 50 ? 'left-1/2 bg-div-pos' : 'right-1/2 bg-div-neg',
              )}
              style={{ width: `${String(Math.abs(bar.percentile - 50))}%` }}
            />
          </span>
          <span className="num t-body-sm text-right text-ink-secondary">{bar.percentile}</span>
        </div>
      ))}
    </div>
  )
}
