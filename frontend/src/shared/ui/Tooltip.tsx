import * as RadixTooltip from '@radix-ui/react-tooltip'
import type { PropsWithChildren, ReactNode } from 'react'

/** Провайдер ставится один раз на приложение: он держит общую задержку и «пропуск»
 * задержки при переходе между соседними подсказками. */
export function TooltipProvider({ children }: PropsWithChildren) {
  return (
    <RadixTooltip.Provider delayDuration={300} skipDelayDuration={150}>
      {children}
    </RadixTooltip.Provider>
  )
}

export interface TooltipProps {
  content: ReactNode
  side?: 'top' | 'right' | 'bottom' | 'left'
  children: ReactNode
}

/** Подсказка — слой над страницей, поэтому единственная (вместе с диалогом и меню)
 * имеет право на тень (§5.7). Появляется мгновенно: `--cv-motion-instant`. */
export function Tooltip({ content, side = 'bottom', children }: TooltipProps) {
  return (
    <RadixTooltip.Root>
      <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
      <RadixTooltip.Portal>
        <RadixTooltip.Content
          side={side}
          sideOffset={6}
          className="t-body-sm z-50 border border-line bg-overlay px-2 py-1 text-ink shadow-overlay"
        >
          {content}
        </RadixTooltip.Content>
      </RadixTooltip.Portal>
    </RadixTooltip.Root>
  )
}
