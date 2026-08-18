import { Outlet, createRootRoute } from '@tanstack/react-router'

/** Корневой роут держит только Outlet: провайдеры (Clerk, Query, тема, подсказки)
 * живут выше роутера, в main.tsx, — они не должны перемонтироваться при навигации. */
export const Route = createRootRoute({
  component: Outlet,
})
