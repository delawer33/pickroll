import { useState } from 'react'

import { SUGGESTED_QUESTIONS } from '@/features/chat/model/suggestions'
import type { AnswerStream, Message, ToolCall } from '@/features/chat/model/types'
import { useConversationStream } from '@/features/chat/model/useConversationStream'
import { Composer } from '@/features/chat/ui/Composer'
import { EvidenceRail } from '@/features/chat/ui/EvidenceRail'
import { MessageList } from '@/features/chat/ui/MessageList'
import { EmptyState } from '@/shared/ui/EmptyState'

export interface ConversationViewProps {
  initialMessages?: Message[]
  initialToolCalls?: ToolCall[]
  /** Источник ответа: за ним SSE Диалога. Тесты подставляют мгновенный поток. */
  stream: AnswerStream
}

/** Структура Worktable: слева Диалог, справа рейка Evidence, у панелей независимый
 * скролл (docs/design-system.md §7.2). Ниже 900px панели складываются в одну колонку —
 * на две ширины больше нет. */
export function ConversationView({
  initialMessages,
  initialToolCalls,
  stream,
}: ConversationViewProps) {
  const conversation = useConversationStream({
    stream,
    ...(initialMessages !== undefined && { initialMessages }),
    ...(initialToolCalls !== undefined && { initialToolCalls }),
  })
  const [activeToolCallId, setActiveToolCallId] = useState<string | null>(null)

  function showEvidence(toolCallId: string) {
    setActiveToolCallId(toolCallId)
    document.getElementById(`evidence-${toolCallId}`)?.scrollIntoView?.({ block: 'nearest' })
  }

  return (
    <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[1fr_auto] shell:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] shell:grid-rows-1">
      <section aria-label="Conversation" className="flex min-h-0 flex-col">
        {conversation.messages.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center overflow-y-auto">
            <EmptyState
              title="Ask about a player, a season or a matchup. The Agent answers from box scores and shows every Tool it called."
              action={
                // Не кнопки-действия: кнопка называет действие коротким глаголом, а
                // это примеры вопросов. Поэтому — набор строк, а не капитель.
                <div className="flex flex-col items-center gap-1">
                  <p className="t-micro pb-1 text-ink-muted">Try</p>
                  {SUGGESTED_QUESTIONS.map((question) => (
                    <button
                      key={question}
                      type="button"
                      className="cv-transition-fast t-body cursor-pointer border-b border-transparent px-1 text-accent-text hover:border-accent"
                      onClick={() => {
                        conversation.ask(question)
                      }}
                    >
                      {question}
                    </button>
                  ))}
                </div>
              }
            />
          </div>
        ) : (
          <MessageList
            messages={conversation.messages}
            onCitationClick={showEvidence}
            onRetry={conversation.retry}
          />
        )}

        <div className="border-t border-line p-4">
          <Composer onSubmit={conversation.ask} disabled={conversation.streaming} />
        </div>
      </section>

      <EvidenceRail toolCalls={conversation.toolCalls} activeToolCallId={activeToolCallId} />
    </div>
  )
}
