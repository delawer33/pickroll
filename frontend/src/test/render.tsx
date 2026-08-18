import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, type RenderResult } from '@testing-library/react'
import type { ReactElement } from 'react'

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
