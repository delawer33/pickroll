import { use } from 'react'

import { ThemeContext, type ThemeContextValue } from '@/shared/theme/themeContext'

export function useTheme(): ThemeContextValue {
  const value = use(ThemeContext)
  if (value === null) {
    throw new Error('useTheme вызван вне ThemeProvider')
  }
  return value
}
