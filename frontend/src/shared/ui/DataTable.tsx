export interface DataTableProps {
  columns: string[]
  rows: string[][]
  /** Подпись для читателя с экранным диктором: таблица без имени не объясняет себя. */
  caption?: string
}

/** Таблица результата: линейки вместо зебры, 2px под шапкой, вертикальные линейки —
 * подпись направления (docs/design-system.md §5.5). Числа выключены вправо и набраны
 * `tnum`, первая колонка — левая и без табличных цифр, в ней имена.
 *
 * Широкий контент скроллится внутри себя: страница по горизонтали не скроллится
 * никогда (§7.3).
 */
export function DataTable({ columns, rows, caption }: DataTableProps) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse">
        {caption !== undefined && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column}
                scope="col"
                className="t-micro border-r border-b-2 border-r-line-row border-b-line-heavy px-2 pt-2 pb-1 text-right whitespace-nowrap text-ink-muted first:text-left last:border-r-0"
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            // Ключ по позиции: строки приходят как есть и могут повторяться значениями
            // (два сезона с одинаковым сплитом).
            <tr key={rowIndex}>
              {row.map((cell, column) => (
                <td
                  key={column}
                  className="num h-row-compact border-r border-b border-r-line-row border-b-line-row px-2 text-right whitespace-nowrap text-ink-secondary first:text-left first:font-medium first:text-ink last:border-r-0"
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
