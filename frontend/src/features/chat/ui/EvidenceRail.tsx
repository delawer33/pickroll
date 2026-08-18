import type { ToolCall } from '@/features/chat/model/types'
import { EvidenceCard } from '@/features/chat/ui/EvidenceCard'

export interface EvidenceRailProps {
  toolCalls: ToolCall[]
  activeToolCallId: string | null
}

/** Правая панель Worktable: накапливает каждый результат Инструмента со штампом
 * источника и времени. Пустая рейка объясняет себя одной строкой — иначе половина
 * экрана выглядит как ошибка вёрстки. */
export function EvidenceRail({ toolCalls, activeToolCallId }: EvidenceRailProps) {
  return (
    <aside
      aria-label="Evidence"
      // В сложенной раскладке рейка ограничена по высоте и скроллится внутри себя:
      // иначе накопленные карточки съедают ленту Диалога вместе с композером.
      className="flex max-h-[45dvh] flex-col gap-2 overflow-y-auto border-t border-line bg-paper p-4 shell:max-h-none shell:border-t-0 shell:border-l"
    >
      <p className="t-micro mb-1 text-ink-muted">
        Evidence
        {toolCalls.length > 0 && ` · ${String(toolCalls.length)}`}
      </p>

      {toolCalls.length === 0 ? (
        <p className="t-body-sm text-ink-muted">
          Every Tool the Agent calls lands here with its source and duration.
        </p>
      ) : (
        toolCalls.map((toolCall) => (
          <EvidenceCard
            key={toolCall.id}
            toolCall={toolCall}
            active={toolCall.id === activeToolCallId}
          />
        ))
      )}
    </aside>
  )
}
