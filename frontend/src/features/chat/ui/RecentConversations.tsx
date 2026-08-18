import { useQuery } from '@tanstack/react-query'

import { listConversations } from '@/features/chat/mock/mockApi'
import { NavItem } from '@/shared/ui/NavItem'
import { Skeleton } from '@/shared/ui/Skeleton'

export interface RecentConversationsProps {
  onNavigate: () => void
}

/** История Диалогов в левой рейке. Живёт в фиче, а не в каркасе: каркас не должен
 * знать ни про Диалог, ни про то, откуда он берётся (docs/design-system.md §9). */
export function RecentConversations({ onNavigate }: RecentConversationsProps) {
  const conversations = useQuery({ queryKey: ['conversations'], queryFn: listConversations })

  if (conversations.isPending) {
    return (
      <div className="flex flex-col gap-1 p-2">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-[70%]" />
      </div>
    )
  }

  if (conversations.isError) {
    // Пустая группа читалась бы как «Диалогов нет» — а их просто не удалось получить.
    return (
      <div className="flex flex-col items-start gap-1 px-2 py-1">
        <p className="t-body-sm text-ink-muted">History unavailable.</p>
        <button
          type="button"
          className="t-body-sm cursor-pointer text-accent-text underline"
          onClick={() => {
            void conversations.refetch()
          }}
        >
          Retry
        </button>
      </div>
    )
  }

  return (
    <>
      {conversations.data.map((conversation) => (
        <NavItem
          key={conversation.id}
          to="/chat/$conversationId"
          params={{ conversationId: conversation.id }}
          icon={<span className="t-micro">{conversation.title.slice(0, 2)}</span>}
          onNavigate={onNavigate}
        >
          <span className="flex min-w-0 flex-col">
            <span className="truncate">{conversation.title}</span>
            <span className="t-micro text-ink-muted">{conversation.when}</span>
          </span>
        </NavItem>
      ))}
    </>
  )
}
