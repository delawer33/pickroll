/** Внешний вид компонентов Clerk, выведенный из дизайн-токенов.
 *
 * Значения читаются из CSS-переменных на `:root` в рантайме, а не дублируются хексами:
 * источник правды один — index.css. Функция вызывается после импорта стилей.
 */
export function clerkAppearance() {
  const token = (name: string): string =>
    getComputedStyle(document.documentElement).getPropertyValue(name).trim()

  return {
    variables: {
      colorPrimary: token('--cv-color-accent'),
      colorText: token('--cv-color-text'),
      colorTextSecondary: token('--cv-color-text-muted'),
      colorBackground: token('--cv-color-surface'),
      colorNeutral: token('--cv-color-text'),
      borderRadius: token('--cv-radius'),
      fontFamily: token('--cv-font-sans'),
    },
  }
}
