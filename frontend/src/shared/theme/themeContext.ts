import { createContext } from 'react'

/** Светлая тема — Slate, тёмная — Night (docs/design-system.md §5.1). Обе темы
 * равноправны: тёмная — выбранные ступени, а не инверсия светлой. */
export type Theme = 'light' | 'dark'

export interface ThemeContextValue {
  theme: Theme
  setTheme: (theme: Theme) => void
}

export const ThemeContext = createContext<ThemeContextValue | null>(null)

/** Ключ в localStorage: выбор темы переживает перезагрузку. */
export const THEME_STORAGE_KEY = 'cv-theme'
