import { useEffect, useState } from 'react'

const QUERY = '(prefers-reduced-motion: reduce)'

/** Настройка «меньше движения» в момент рендера. Токены гасят переходы сами, но
 * анимацию внутри библиотеки графиков приходится выключать явно (§6.2). */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => window.matchMedia(QUERY).matches)

  useEffect(() => {
    const media = window.matchMedia(QUERY)
    const update = () => {
      setReduced(media.matches)
    }
    media.addEventListener('change', update)
    return () => {
      media.removeEventListener('change', update)
    }
  }, [])

  return reduced
}
