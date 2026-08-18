import { useCallback, useLayoutEffect, useRef, type TextareaHTMLAttributes } from 'react'

import { cn } from '@/shared/ui/cn'

export interface TextAreaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  /** Сколько строк показывать максимум, дальше появляется скролл. */
  maxRows?: number
}

/** Поле ввода, растущее под содержимое: композер обязан показывать весь вопрос,
 * но не имеет права занять экран целиком. Высота считается по `scrollHeight`, для
 * чего её сначала нужно сбросить — иначе поле умеет только расти. */
export function TextArea({ maxRows = 8, className, onChange, ...props }: TextAreaProps) {
  const ref = useRef<HTMLTextAreaElement>(null)

  const resize = useCallback(() => {
    const element = ref.current
    if (element === null) {
      return
    }
    // Интерлиньяж и отступы читаются со стиля, а не дублируются числами: ступень
    // типографики живёт в type.css и может измениться там (jsdom отдаёт 'normal').
    const style = getComputedStyle(element)
    const lineHeight = Number.parseFloat(style.lineHeight) || 21
    const padding =
      (Number.parseFloat(style.paddingTop) || 0) + (Number.parseFloat(style.paddingBottom) || 0)
    const max = maxRows * lineHeight + padding
    element.style.height = 'auto'
    element.style.height = `${String(Math.min(element.scrollHeight, max))}px`
    element.style.overflowY = element.scrollHeight > max ? 'auto' : 'hidden'
  }, [maxRows])

  // Layout-эффект, а не effect: значение может прийти извне (пример вопроса из
  // пустого состояния), и одна кадровая вспышка неправильной высоты была бы видна.
  useLayoutEffect(resize, [resize, props.value])

  return (
    <textarea
      ref={ref}
      rows={1}
      className={cn(
        't-body block w-full resize-none bg-transparent px-3 py-2 text-ink',
        'placeholder:text-ink-muted focus-visible:outline-none',
        className,
      )}
      onChange={(event) => {
        resize()
        onChange?.(event)
      }}
      {...props}
    />
  )
}
