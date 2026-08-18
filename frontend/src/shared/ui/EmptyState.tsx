import type { ReactNode } from 'react'

import { cn } from '@/shared/ui/cn'

export interface EmptyStateProps {
  title: string
  /** Одно действие, не список возможностей. */
  action?: ReactNode
  className?: string
}

/** Пустое состояние: одна строка о том, что это, и одно действие. Единственное место в
 * приложении, где текст центрируется (§3, §7.5). */
export function EmptyState({ title, action, className }: EmptyStateProps) {
  return (
    <div className={cn('flex flex-col items-center gap-4 px-4 py-12 text-center', className)}>
      <p className="t-body max-w-read text-ink-secondary">{title}</p>
      {action}
    </div>
  )
}
