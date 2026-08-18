import { createFileRoute, redirect } from '@tanstack/react-router'

/** Корень ведёт на Диалог: это главный экран продукта (§8). Редирект в `beforeLoad`,
 * чтобы промежуточный пустой экран не отрисовывался вовсе. */
export const Route = createFileRoute('/')({
  beforeLoad: () => {
    throw redirect({ to: '/chat' })
  },
})
