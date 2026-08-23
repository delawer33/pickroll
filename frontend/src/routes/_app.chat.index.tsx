import { useQueryClient } from '@tanstack/react-query'
import { createFileRoute, useRouterState } from '@tanstack/react-router'
import { useMemo, useRef } from 'react'

import { createAnswerStream } from '@/features/chat/api/answerStream'
import { ConversationView } from '@/features/chat/ui/ConversationView'
import { useApiClient } from '@/shared/api/useApiClient'

/** Новый Диалог: лента пуста, вопрос задаётся композером или примером из пустого
 * состояния. Сам Диалог заводится первым вопросом — заранее созданный оставлял бы
 * пустышку в истории на каждый заход на страницу. */
export const Route = createFileRoute('/_app/chat/')({
  staticData: { title: 'New conversation' },
  component: NewConversationRoute,
})

function NewConversationRoute() {
  // «New conversation» из рейки ведёт на тот же `/chat`: без смены ключа React
  // переиспользовал бы компонент вместе с уже накопленной лентой, и новый Диалог
  // оказался бы продолжением старого.
  const seed = useRouterState({ select: (state) => state.location.state.conversationSeed })

  // Ключ на этом уровне, а не на `ConversationView`: поток помнит идентификатор Диалога,
  // созданного первым вопросом, и обязан пересоздаваться вместе с лентой. Оставь ключ
  // ниже — новый Диалог продолжил бы дописываться в предыдущий.
  return <NewConversation key={seed ?? 'first'} />
}

function NewConversation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  // Диалог, заведённый первым вопросом. В ref, а не в замыкании потока: клиент
  // пересоздаётся при обновлении токена Clerk, а Диалог остаётся тем же.
  const conversation = useRef<string | undefined>(undefined)

  const stream = useMemo(
    () =>
      createAnswerStream({
        client,
        conversation,
        onConversationCreated: () => {
          // Диалог появился в истории — рейка обязана его показать, не дожидаясь
          // перезагрузки. URL при этом не меняем: переход на `/chat/{id}` пересоздал бы
          // компонент и оборвал поток на середине ответа.
          void queryClient.invalidateQueries({ queryKey: ['conversations'] })
        },
      }),
    [client, queryClient],
  )

  return <ConversationView stream={stream} />
}
