import { useCallback, useState, type PropsWithChildren, type ReactNode } from 'react'

import { Rail } from '@/app/shell/Rail'
import { TopBar } from '@/app/shell/TopBar'

export interface AppShellProps {
  title: string
  /** Что показать в группе «Recent» левой рейки — передаёт композиция роутов. */
  recent?: (options: { onNavigate: () => void }) => ReactNode
}

/** Каркас: постоянная левая рейка, верхняя полоса 46px, дальше область контента (§8).
 * Ниже 900px рейка становится drawer поверх контента — колонок на неё больше нет. */
export function AppShell({ title, recent, children }: PropsWithChildren<AppShellProps>) {
  const [collapsed, setCollapsed] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)

  // Переход по любому пункту закрывает drawer: иначе на узком экране он остался бы
  // поверх только что открытого экрана.
  const closeDrawer = useCallback(() => {
    setDrawerOpen(false)
  }, [])

  return (
    <div className="flex h-dvh w-full overflow-hidden bg-surface">
      <Rail
        collapsed={collapsed}
        onCollapsedChange={setCollapsed}
        open={drawerOpen}
        onNavigate={closeDrawer}
        recent={recent?.({ onNavigate: closeDrawer })}
      />

      {/* Подложка гасит drawer по клику мимо. На широком экране рейка статична и
          подложки не существует. */}
      {drawerOpen && (
        <button
          type="button"
          aria-label="Close navigation"
          className="fixed inset-0 z-30 bg-ink/30 shell:hidden"
          onClick={() => {
            setDrawerOpen(false)
          }}
        />
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar
          title={title}
          onOpenNavigation={() => {
            setDrawerOpen(true)
          }}
        />
        <main className="flex min-h-0 flex-1 flex-col">{children}</main>
      </div>
    </div>
  )
}
