/** Когда это было — относительной подписью под заголовком Диалога.
 *
 * Относительное время, а не дата: в истории важно «сегодня или на прошлой неделе», а не
 * точный штамп. Формат зависит от локали браузера, поэтому собирается `Intl`, а не строками.
 */

const UNITS: Array<[Intl.RelativeTimeFormatUnit, number]> = [
  ['year', 365 * 24 * 60 * 60 * 1000],
  ['month', 30 * 24 * 60 * 60 * 1000],
  ['week', 7 * 24 * 60 * 60 * 1000],
  ['day', 24 * 60 * 60 * 1000],
  ['hour', 60 * 60 * 1000],
  ['minute', 60 * 1000],
]

export function formatWhen(iso: string, now: Date = new Date()): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) {
    // Битую метку показываем как есть: пустая подпись читалась бы как «неизвестно когда»,
    // а это ошибка данных, которую видно.
    return iso
  }

  const elapsed = at.getTime() - now.getTime()
  const format = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
  for (const [unit, ms] of UNITS) {
    if (Math.abs(elapsed) >= ms) {
      // trunc, а не round: округление вверх перескакивает выбранную единицу — Диалог
      // сорокапятидневной давности становился бы «2 months ago».
      return format.format(Math.trunc(elapsed / ms), unit)
    }
  }
  return format.format(0, 'minute')
}
