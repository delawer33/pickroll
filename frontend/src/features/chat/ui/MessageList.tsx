import { useEffect, useRef } from 'react'

import type { Message as MessageModel } from '@/features/chat/model/types'
import { Message } from '@/features/chat/ui/Message'

export interface MessageListProps {
  messages: MessageModel[]
  onCitationClick: (toolCallId: string) => void
  onRetry: (agentMessageId: string) => void
}

/** Лента Диалога. Скроллится сама, независимо от рейки Evidence: у панелей разные
 * задачи и разная длина (§7.2). */
export function MessageList({ messages, onCitationClick, onRetry }: MessageListProps) {
  const bottom = useRef<HTMLDivElement>(null)
  const lastMessage = messages.at(-1)
  const streamedLength = lastMessage?.role === 'agent' ? lastMessage.text.length : 0

  // Держим низ ленты в поле зрения, пока идёт ответ.
  useEffect(() => {
    bottom.current?.scrollIntoView?.({ block: 'end' })
  }, [messages.length, streamedLength])

  return (
    <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-4">
      {messages.map((message) => (
        <Message
          key={message.id}
          message={message}
          onCitationClick={onCitationClick}
          onRetry={onRetry}
        />
      ))}
      <div ref={bottom} />
    </div>
  )
}
