import { Link, type LinkProps } from '@tanstack/react-router'
import type { ReactNode } from 'react'

export interface NavItemProps extends LinkProps {
  icon: ReactNode
  /** В свёрнутой рейке остаётся только иконка. */
  collapsed?: boolean
  onNavigate?: () => void
  children: ReactNode
}

/** Пункт левой рейки. Активный: фон ступени 5 и 2px акцентная планка слева —
 * единственное место, где планка разрешена (docs/design-system.md §3, §8). */
export function NavItem({ icon, collapsed = false, onNavigate, children, ...link }: NavItemProps) {
  return (
    <Link
      {...link}
      onClick={onNavigate}
      activeOptions={{ exact: true }}
      className="cv-transition-fast t-body flex min-w-0 items-center gap-2 border-l-2 border-transparent px-2 py-1 text-ink-secondary hover:bg-fill data-[status=active]:border-l-accent data-[status=active]:bg-fill-active data-[status=active]:text-ink"
    >
      <span className="flex h-4 w-4 shrink-0 items-center justify-center text-ink-muted">
        {icon}
      </span>
      {!collapsed && children}
    </Link>
  )
}
