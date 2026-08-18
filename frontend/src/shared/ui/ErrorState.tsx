import { AlertTriangle } from 'lucide-react'

import type { Problem } from '@/shared/api/client'
import { Button } from '@/shared/ui/Button'
import { cn } from '@/shared/ui/cn'

export interface ErrorStateProps {
  problem: Problem
  onRetry?: () => void
  className?: string
}

/** Состояние ошибки говорит, что случилось и что делать: заголовок и текст берутся из
 * Problem Details, а не подменяются на «Something went wrong». Иконка идёт вместе с
 * подписью — цвет не единственный носитель смысла. */
export function ErrorState({ problem, onRetry, className }: ErrorStateProps) {
  return (
    <div className={cn('flex gap-3 border border-line bg-surface p-4', className)} role="alert">
      <AlertTriangle aria-hidden="true" size={16} className="mt-1 shrink-0 text-critical" />
      <div className="flex flex-col items-start gap-2">
        <p className="t-heading text-critical">{problem.title}</p>
        <p className="t-body max-w-read text-ink-secondary">{problem.detail}</p>
        {onRetry !== undefined && (
          <Button onClick={onRetry} className="mt-1">
            Retry
          </Button>
        )}
      </div>
    </div>
  )
}
