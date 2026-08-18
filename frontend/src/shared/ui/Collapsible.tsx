import * as RadixCollapsible from '@radix-ui/react-collapsible'
import { ChevronDown } from 'lucide-react'
import type { ReactNode } from 'react'

export interface CollapsibleProps {
  /** Видимая часть: остаётся на экране в свёрнутом виде. */
  summary: ReactNode
  defaultOpen?: boolean
  children: ReactNode
}

/** Свёртывание на примитиве Radix: клавиатура, `aria-expanded` и связь триггера с
 * содержимым уже правильные, стилизация наша. Шеврон поворачивается, содержимое не
 * анимируется — высота списка меняется слишком часто, чтобы это было полезно. */
export function Collapsible({ summary, defaultOpen = true, children }: CollapsibleProps) {
  return (
    <RadixCollapsible.Root defaultOpen={defaultOpen} className="min-w-0">
      {/* min-w-0 по всей цепочке: без него grid-элемент считает минимальной шириной
          ширину содержимого, и длинная строка распирает карточку вместо переноса. */}
      <RadixCollapsible.Trigger className="group flex w-full min-w-0 cursor-pointer items-baseline gap-2 text-left">
        <ChevronDown
          aria-hidden="true"
          size={12}
          className="cv-transition-fast shrink-0 translate-y-[1px] text-ink-muted group-data-[state=closed]:-rotate-90"
        />
        {summary}
      </RadixCollapsible.Trigger>
      <RadixCollapsible.Content className="min-w-0">{children}</RadixCollapsible.Content>
    </RadixCollapsible.Root>
  )
}
