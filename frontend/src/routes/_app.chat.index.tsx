import { createFileRoute, useRouterState } from '@tanstack/react-router'

import { ConversationView } from '@/features/chat/ui/ConversationView'

/** Новый Диалог: лента пуста, вопрос задаётся композером или примером из пустого
 * состояния. */
export const Route = createFileRoute('/_app/chat/')({
  staticData: { title: 'New conversation' },
  component: NewConversationRoute,
})

function NewConversationRoute() {
  // «New conversation» из рейки ведёт на тот же `/chat`: без смены ключа React
  // переиспользовал бы компонент вместе с уже накопленной лентой, и новый Диалог
  // оказался бы продолжением старого.
  const seed = useRouterState({ select: (state) => state.location.state.conversationSeed })

  return <ConversationView key={seed ?? 'first'} />
}
