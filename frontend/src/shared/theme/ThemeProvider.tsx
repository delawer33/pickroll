import { useCallback, useEffect, useMemo, useState, type PropsWithChildren } from 'react'

import { THEME_STORAGE_KEY, ThemeContext, type Theme } from '@/shared/theme/themeContext'

/** Тема выбирается в порядке: сохранённый выбор → системная настройка → светлая.
 * Читается синхронно при инициализации состояния, иначе первый кадр отрисуется
 * светлым и мигнёт. */
function initialTheme(): Theme {
  // localStorage бросает там, где хранилище запрещено (Safari с блокировкой cookies,
  // партиционированный iframe). Провайдер стоит выше любого error boundary, так что
  // необработанное исключение здесь — белый экран вместо приложения.
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY)
    if (stored === 'light' || stored === 'dark') {
      return stored
    }
  } catch {
    /* выбор не сохранён — берём системную настройку */
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function ThemeProvider({ children }: PropsWithChildren) {
  const [theme, setThemeState] = useState<Theme>(initialTheme)

  // Атрибут на <html>: тема переключается переопределением ramp в tokens.css,
  // компоненты о ней не знают.
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    // Нативные контролы (скроллбары, автозаполнение) должны попасть в ту же тему.
    document.documentElement.style.colorScheme = theme
  }, [theme])

  const setTheme = useCallback((next: Theme) => {
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next)
    } catch {
      /* тема переключится, но не переживёт перезагрузку — это лучше, чем падение */
    }
    setThemeState(next)
  }, [])

  const value = useMemo(() => ({ theme, setTheme }), [theme, setTheme])

  return <ThemeContext value={value}>{children}</ThemeContext>
}
