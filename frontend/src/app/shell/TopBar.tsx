import { UserButton } from '@clerk/clerk-react'
import { Menu } from 'lucide-react'

import { ThemeToggle } from '@/app/shell/ThemeToggle'
import { IconButton } from '@/shared/ui/IconButton'

export interface TopBarProps {
  title: string
  onOpenNavigation: () => void
}

/** Верхняя полоса 46px: заголовок экрана слева, действия справа. Кнопка меню
 * появляется только там, где рейка уехала в drawer. */
export function TopBar({ title, onOpenNavigation }: TopBarProps) {
  return (
    <header className="flex h-topbar shrink-0 items-center gap-2 border-b border-line bg-paper px-4">
      <IconButton
        label="Open navigation"
        icon={<Menu size={16} />}
        onClick={onOpenNavigation}
        className="shell:hidden"
      />
      <h1 className="t-heading truncate text-ink">{title}</h1>
      <div className="ml-auto flex items-center gap-2">
        <ThemeToggle />
        <UserButton />
      </div>
    </header>
  )
}
