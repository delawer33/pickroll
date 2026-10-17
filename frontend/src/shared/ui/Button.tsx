import type { ButtonHTMLAttributes } from 'react'

import { buttonClasses, type ButtonSize, type ButtonVariant } from '@/shared/ui/buttonClasses'
import { cn } from '@/shared/ui/cn'

export type { ButtonSize, ButtonVariant }

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
}

/** Кнопка называет действие (`Generate report`). Стиль — в `buttonClasses`. */
export function Button({
  variant = 'ghost',
  size = 'md',
  className,
  type = 'button',
  ...props
}: ButtonProps) {
  return <button type={type} className={cn(buttonClasses(variant, size), className)} {...props} />
}
