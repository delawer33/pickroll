import { useQuery } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'

import { fetchConversation } from '@/features/chat/mock/mockApi'
import { ConversationSkeleton } from '@/features/chat/ui/ConversationSkeleton'
import { ConversationView } from '@/features/chat/ui/ConversationView'
import { ApiError } from '@/shared/api/client'
import { ErrorState } from '@/shared/ui/ErrorState'

/** Сохранённый Диалог. Данные читаются TanStack Query — серверное состояние живёт
 * только в нём; своего стейт-менеджера у приложения нет. */
export const Route = createFileRoute('/_app/chat/$conversationId')({
  staticData: { title: 'Conversation' },
  component: ConversationRoute,
})

function ConversationRoute() {
  const { conversationId } = Route.useParams()
  const conversation = useQuery({
    queryKey: ['conversation', conversationId],
    queryFn: () => fetchConversation(conversationId),
    retry: false,
  })

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
      initialMessages={conversation.data.messages}
      initialToolCalls={conversation.data.toolCalls}
    />
  )
}
