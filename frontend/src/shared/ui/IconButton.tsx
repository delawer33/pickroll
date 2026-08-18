import type { ButtonHTMLAttributes, ReactNode } from 'react'

import { cn } from '@/shared/ui/cn'

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Обязательна: иконка сама по себе имени кнопке не даёт. */
  label: string
  icon: ReactNode
}

/** Квадратная кнопка с одной иконкой Lucide. Подпись уходит в `aria-label`, а видимой
 * её делает `Tooltip` вокруг — иконка без подписи угадывается неверно. */
export function IconButton({ label, icon, className, type = 'button', ...props }: IconButtonProps) {
  return (
    <button
      type={type}
      aria-label={label}
      className={cn(
        'cv-transition-fast inline-flex h-control-md w-control-md cursor-pointer items-center justify-center',
        'border border-transparent text-ink-secondary hover:bg-fill hover:text-ink',
        className,
      )}
      {...props}
    >
      {icon}
    </button>
  )
}
