import { useQuery } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'
import { useMemo } from 'react'

import { createAnswerStream } from '@/features/chat/api/answerStream'
import { fetchMessages } from '@/features/chat/api/conversations'
import { ConversationSkeleton } from '@/features/chat/ui/ConversationSkeleton'
import { ConversationView } from '@/features/chat/ui/ConversationView'
import { ApiError } from '@/shared/api/client'
import { useApiClient } from '@/shared/api/useApiClient'
import { ErrorState } from '@/shared/ui/ErrorState'

/** Сохранённый Диалог. Данные читаются TanStack Query — серверное состояние живёт
 * только в нём; своего стейт-менеджера у приложения нет. */
export const Route = createFileRoute('/_app/chat/$conversationId')({
  staticData: { title: 'Conversation' },
  component: ConversationRoute,
})

function ConversationRoute() {
  const { conversationId } = Route.useParams()
  const client = useApiClient()

  const conversation = useQuery({
    queryKey: ['conversation', conversationId],
    queryFn: () => fetchMessages(client, conversationId),
    // Чужой или удалённый Диалог — это 404, и повторять запрос незачем.
    retry: false,
  })

  const stream = useMemo(
    // Диалог уже существует — идентификатор берётся из адреса и не меняется.
    () => createAnswerStream({ client, conversation: { current: conversationId } }),
    [client, conversationId],
  )

  if (conversation.isPending) {
    return <ConversationSkeleton />
  }

  if (conversation.isError) {
    const problem =
      conversation.error instanceof ApiError
        ? conversation.error.problem
        : {
            type: 'about:blank',
            title: 'Conversation failed to load',
            status: 0,
            detail: 'Something went wrong while loading this conversation. Retry to try again.',
            instance: `/api/v1/conversations/${conversationId}`,
          }
    return (
      <div className="p-6">
        <ErrorState
          problem={problem}
          onRetry={() => {
            void conversation.refetch()
          }}
        />
      </div>
    )
  }

  return (
    // key: у каждого Диалога своя лента, и переход между ними обязан начинать её
    // заново, а не дописывать в предыдущую.
    <ConversationView
      key={conversationId}
      stream={stream}
      initialMessages={conversation.data}
      initialToolCalls={[]}
    />
  )
}
