import { ClerkProvider } from '@clerk/clerk-react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useMemo, type PropsWithChildren } from 'react'

import { clerkAppearance } from '@/shared/styles/clerkAppearance'
import { ThemeProvider } from '@/shared/theme/ThemeProvider'
import { useTheme } from '@/shared/theme/useTheme'
import { TooltipProvider } from '@/shared/ui/Tooltip'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Статистика меняется раз в сутки, а не поминутно: перезапрашивать её на
      // каждый возврат во вкладку незачем.
      staleTime: 60_000,
      refetchOnWindowFocus: false,
    },
  },
})

/** Провайдеры приложения. Стоят выше роутера: смена экрана не должна ронять сессию
 * Clerk, кеш запросов или тему. */
export function Providers({ publishableKey, children }: PropsWithChildren<{ publishableKey: string }>) {
  return (
    <ThemeProvider>
      <ClerkWithTheme publishableKey={publishableKey}>
        <QueryClientProvider client={queryClient}>
          <TooltipProvider>{children}</TooltipProvider>
        </QueryClientProvider>
      </ClerkWithTheme>
    </ThemeProvider>
  )
}

/** Внешний вид Clerk пересчитывается при смене темы: его компоненты живут в своих
 * стилях и наших CSS-переменных не видят. */
function ClerkWithTheme({ publishableKey, children }: PropsWithChildren<{ publishableKey: string }>) {
  const { theme } = useTheme()
  const appearance = useMemo(() => clerkAppearance(theme), [theme])

  return (
    <ClerkProvider publishableKey={publishableKey} signInUrl="/sign-in" appearance={appearance}>
      {children}
    </ClerkProvider>
  )
}
