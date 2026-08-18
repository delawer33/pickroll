import { splitAnswer } from '@/features/chat/model/splitAnswer'
import type { Message as MessageModel } from '@/features/chat/model/types'
import { TypingCursor } from '@/features/chat/ui/TypingCursor'
import { ErrorState } from '@/shared/ui/ErrorState'

export interface MessageProps {
  message: MessageModel
  onCitationClick: (toolCallId: string) => void
  onRetry: (agentMessageId: string) => void
}

/** Реплика пользователя и ответ Агента набраны по-разному не для украшения: реплика —
 * короткая, выключена вправо и живёт на заливке; ответ — текст для чтения в колонке
 * 68ch без фона и рамки (§7.2). */
export function Message({ message, onCitationClick, onRetry }: MessageProps) {
  if (message.role === 'user') {
    return (
      <div className="cv-enter flex justify-end">
        <p className="t-body max-w-[80%] bg-fill px-3 py-2 text-ink">{message.text}</p>
      </div>
    )
  }

  return (
    <div className="cv-enter flex flex-col gap-3">
      {message.text !== '' && (
        <p className="t-read max-w-read text-ink">
          {splitAnswer(message.text, message.citations).map((part, position) => {
            if (part.kind === 'text') {
              return <span key={position}>{part.text}</span>
            }
            const { toolCallId } = part
            return (
              // Ступень `micro` без капители: это число, а не метка колонки.
              <sup
                key={position}
                className="t-micro align-super font-bold tracking-normal normal-case text-accent-text"
              >
                {toolCallId === undefined ? (
                  part.index
                ) : (
                  <button
                    type="button"
                    className="cursor-pointer"
                    aria-label={`Source ${String(part.index)}`}
                    onClick={() => {
                      onCitationClick(toolCallId)
                    }}
                  >
                    {part.index}
                  </button>
                )}
              </sup>
            )
          })}
          {message.streaming && <TypingCursor />}
        </p>
      )}

      {/* Пока не пришло ни одного токена, курсор — единственный признак работы. */}
      {message.text === '' && message.streaming && (
        <p className="t-read text-ink">
          <TypingCursor />
        </p>
      )}

      {message.error !== undefined && (
        <ErrorState
          problem={message.error}
          onRetry={() => {
            onRetry(message.id)
          }}
          className="max-w-read"
        />
      )}
    </div>
  )
}
