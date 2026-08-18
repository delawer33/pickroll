/** Склейка классов: `false`, `null` и `undefined` отбрасываются.
 *
 * Свой хелпер вместо clsx/tailwind-merge: конфликтов утилит мы не разрешаем — вариант
 * компонента задаёт полный набор классов, а не доклеивает поверх чужого. */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ')
}
