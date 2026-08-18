import type { ButtonHTMLAttributes } from 'react'

import { cn } from '@/shared/ui/cn'

export type ButtonVariant = 'solid' | 'ghost'
export type ButtonSize = 'sm' | 'md' | 'lg'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
}

const VARIANT: Record<ButtonVariant, string> = {
  solid: 'bg-accent-solid text-accent-on border-accent-solid hover:bg-accent-text',
  ghost: 'bg-transparent text-ink border-line hover:bg-fill',
}

const SIZE: Record<ButtonSize, string> = {
  sm: 'h-control-sm px-2',
  md: 'h-control-md px-3',
  lg: 'h-control-lg px-4',
}

/** Кнопка называет действие (`Generate report`), поэтому подпись набирается капителью
 * `label`-ступени — короткая, плотная и без риска переноса. Радиуса и тени нет. */
export function Button({
  variant = 'ghost',
  size = 'md',
  className,
  type = 'button',
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(
        't-label inline-flex cursor-pointer items-center justify-center gap-2 border font-semibold tracking-[0.06em] uppercase',
        'cv-transition-fast disabled:cursor-not-allowed disabled:opacity-50',
        VARIANT[variant],
        SIZE[size],
        className,
      )}
      {...props}
    />
  )
}
