import { Moon, Sun } from 'lucide-react'

import { useTheme } from '@/shared/theme/useTheme'
import { IconButton } from '@/shared/ui/IconButton'
import { Tooltip } from '@/shared/ui/Tooltip'

/** Обе темы равноправны, поэтому переключатель живёт в верхней полосе, а не в
 * настройках: выбор запоминается (localStorage), по умолчанию берётся системный. */
export function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  const next = theme === 'dark' ? 'light' : 'dark'
  const label = next === 'dark' ? 'Switch to dark theme' : 'Switch to light theme'

  return (
    <Tooltip content={label}>
      <IconButton
        label={label}
        icon={theme === 'dark' ? <Moon size={16} /> : <Sun size={16} />}
        onClick={() => {
          setTheme(next)
        }}
      />
    </Tooltip>
  )
}
