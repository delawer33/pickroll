import { createRouter } from '@tanstack/react-router'

import { routeTree } from '@/routeTree.gen'

/** Дерево роутов генерируется плагином из `src/routes` и коммитится в репозиторий:
 * без него не проходит `tsc --noEmit` в CI, где сборка не запускалась. */
export const router = createRouter({
  routeTree,
  defaultPreload: 'intent',
})

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }

  /** Заголовок экрана для верхней полосы: объявляет роут, читает каркас (§8). */
  interface StaticDataRouteOption {
    title?: string
  }

  interface HistoryState {
    /** Метка «это другой Диалог» для `/chat`. Путь у нового Диалога тот же, поэтому
     * без неё переход с `/chat` на `/chat` переиспользовал бы уже набранную ленту. */
    conversationSeed?: string
  }
}
