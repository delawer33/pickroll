/** Курсор печати: стриминг обозначает курсор, а не декор (§7.2). 2px × 1em, цвет
 * акцента, мигание 1.1s — единственный бесконечный цикл в приложении. */
export function TypingCursor() {
  return (
    <span
      data-testid="typing-cursor"
      aria-hidden="true"
      className="cv-cursor ml-[2px] inline-block h-[1em] w-[2px] translate-y-[2px] bg-accent"
    />
  )
}
