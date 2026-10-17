import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { render, waitFor, type RenderResult } from '@testing-library/react'
import type { ReactElement } from 'react'
import { expect } from 'vitest'

import { ThemeProvider } from '@/shared/theme/ThemeProvider'
import { TooltipProvider } from '@/shared/ui/Tooltip'

/** Рендер с провайдерами, без которых компоненты не работают: тема, подсказки Radix и
 * кеш запросов. Повторы выключены — иначе ожидаемая ошибка ждёт бэкоффа. */
export function renderWithProviders(ui: ReactElement): RenderResult {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })

  return render(
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>{ui}</TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>,
  )
}

/** То же, но внутри минимального роутера: для компонентов со ссылками (`Link`) без
 * подъёма всего дерева роутов и Clerk. Ссылки строятся по настоящему шаблону пути страницы
 * отчёта, поэтому `href` в тесте — тот же, что в приложении. */
export async function renderWithRouter(ui: ReactElement): Promise<RenderResult> {
  const root = createRootRoute({ component: () => ui })
  const report = createRoute({
    getParentRoute: () => root,
    path: '/reports/$reportId',
    component: () => null,
  })
  const router = createRouter({
    routeTree: root.addChildren([report]),
    history: createMemoryHistory({ initialEntries: ['/'] }),
  })
  await router.load()

  const rendered = renderWithProviders(<RouterProvider router={router} />)
  await waitFor(() => {
    expect(rendered.container).not.toBeEmptyDOMElement()
  })
  return rendered
}
