import { ClerkLoading, RedirectToSignIn, SignedIn, SignedOut } from '@clerk/clerk-react'
import { Outlet, createFileRoute, useMatches } from '@tanstack/react-router'

import { AppShell } from '@/app/shell/AppShell'
import { RecentConversations } from '@/features/chat/ui/RecentConversations'
import { Skeleton } from '@/shared/ui/Skeleton'

/** Пластовый роут: всё, что внутри, требует сессии и рисуется в каркасе. Путь он не
 * добавляет — `/chat` остаётся `/chat`. */
export const Route = createFileRoute('/_app')({
  component: AppLayout,
})

function AppLayout() {
  const matches = useMatches()
  // Заголовок экрана объявляет сам роут: верхняя полоса не должна знать про домен.
  const title =
    matches.findLast((match) => match.staticData.title !== undefined)?.staticData.title ??
    'Court Vision'

  return (
    <>
      {/* Пока clerk-js грузится, SignedIn и SignedOut оба рендерят null — без этой
          ветки заблокированный скрипт Clerk выглядел бы как вечно пустая страница. */}
      <ClerkLoading>
        <div className="flex h-dvh flex-col gap-2 bg-paper p-4" role="status" aria-label="Loading">
          <Skeleton className="h-topbar w-full" />
          <Skeleton className="flex-1 w-full" />
        </div>
      </ClerkLoading>
      <SignedIn>
        <AppShell
          title={title}
          recent={({ onNavigate }) => <RecentConversations onNavigate={onNavigate} />}
        >
          <Outlet />
        </AppShell>
      </SignedIn>
      <SignedOut>
        <RedirectToSignIn />
      </SignedOut>
    </>
  )
}
