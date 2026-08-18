import { PanelLeftClose, PanelLeftOpen, SquarePen } from 'lucide-react'
import type { ReactNode } from 'react'

import { NavItem } from '@/shared/ui/NavItem'
import { Tooltip } from '@/shared/ui/Tooltip'
import { cn } from '@/shared/ui/cn'

export interface RailProps {
  collapsed: boolean
  onCollapsedChange: (collapsed: boolean) => void
  /** Открыта как drawer на узком экране. */
  open: boolean
  onNavigate: () => void
  /** Группа «Recent» приходит от фичи: каркас не знает, что такое Диалог (§9). */
  recent?: ReactNode
}

/** Постоянная левая рейка. Вертикаль выбрана вместо верхней навигации потому, что
 * Диалогов много и их истории нужна высота, а продукт клавиатурный (§8). */
export function Rail({ collapsed, onCollapsedChange, open, onNavigate, recent }: RailProps) {
  return (
    <aside
      aria-label="Navigation"
      className={cn(
        'cv-transition-panel z-40 flex shrink-0 flex-col border-r border-line bg-paper',
        'fixed inset-y-0 left-0 shell:static',
        open ? 'flex' : 'hidden shell:flex',
        collapsed ? 'w-rail-collapsed' : 'w-rail',
      )}
    >
      <div className="flex h-topbar shrink-0 items-center border-b border-line px-3">
        <span className={cn('t-micro text-ink', collapsed && 'sr-only')}>Court Vision</span>
      </div>

      <nav className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto p-2">
        <RailGroup label="Assistant" collapsed={collapsed} />
        <NavItem
          to="/chat"
          // Метка ставится в момент клика: она и отличает новый Диалог от текущего,
          // путь-то один и тот же.
          state={(previous) => ({ ...previous, conversationSeed: crypto.randomUUID() })}
          collapsed={collapsed}
          icon={<SquarePen size={14} />}
          onNavigate={onNavigate}
        >
          New conversation
        </NavItem>

        <RailGroup label="Recent" collapsed={collapsed} />
        {!collapsed && recent}
      </nav>

      <div className="hidden shrink-0 border-t border-line p-2 shell:block">
        <Tooltip content={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} side="right">
          <button
            type="button"
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            aria-pressed={collapsed}
            onClick={() => {
              onCollapsedChange(!collapsed)
            }}
            className="cv-transition-fast flex h-control-md w-full cursor-pointer items-center justify-center text-ink-muted hover:bg-fill hover:text-ink"
          >
            {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
          </button>
        </Tooltip>
      </div>
    </aside>
  )
}

/** Заголовок группы не кликабелен — это метка, а не пункт (§8). */
function RailGroup({ label, collapsed }: { label: string; collapsed: boolean }) {
  return (
    <p className={cn('t-micro px-2 pt-3 pb-1 text-ink-muted', collapsed && 'sr-only')}>{label}</p>
  )
}
