import type { Theme } from '@/shared/theme/themeContext'

/** Внешний вид компонентов Clerk, выведенный из дизайн-токенов.
 *
 * Значения читаются из CSS-переменных, а не дублируются хексами: источник правды один —
 * tokens.css. Передать Clerk `var(--cv-accent)` нельзя — он парсит цвет, чтобы построить
 * производные оттенки, и на `var()` спотыкается.
 *
 * Замер идёт не с `<html>`, а с временного узла, помеченного нужной темой: appearance
 * считается во время рендера, а атрибут на документе выставляет эффект — то есть на
 * переключении темы с документа читались бы значения предыдущей.
 */
export function clerkAppearance(theme: Theme) {
  const probe = document.createElement('div')
  probe.dataset.theme = theme
  probe.style.display = 'none'
  document.body.appendChild(probe)
  const computed = getComputedStyle(probe)
  const token = (name: string): string => computed.getPropertyValue(name).trim()

  const appearance = {
    variables: {
      colorPrimary: token('--cv-accent-solid'),
      colorTextOnPrimaryBackground: token('--cv-accent-on'),
      colorText: token('--cv-ink'),
      colorTextSecondary: token('--cv-ink-muted'),
      colorBackground: token('--cv-surface'),
      colorInputBackground: token('--cv-surface'),
      colorInputText: token('--cv-ink'),
      colorNeutral: token('--cv-ink'),
      colorDanger: token('--cv-critical'),
      colorSuccess: token('--cv-good'),
      colorWarning: token('--cv-warning'),
      // Радиус 0 везде; круг остаётся только у аватара — его Clerk рисует сам.
      borderRadius: '0',
      fontFamily: token('--cv-font'),
      fontSize: '14px',
    },
  }

  probe.remove()
  return appearance
}
