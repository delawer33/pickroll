import { useCallback, useEffect, useRef, useState } from 'react'

import type {
  AgentMessage,
  AnswerStream,
  Message,
  StreamEvent,
  ToolCall,
} from '@/features/chat/model/types'

export interface UseConversationStreamOptions {
  /** Источник потока: за ним SSE-эндпоинт Диалога. Значения по умолчанию нет намеренно —
   * лента без источника событий не имеет смысла, а мок бы её тихо подменял. */
  stream: AnswerStream
  /** Уже сохранённая часть Диалога (пришла из API до монтирования). */
  initialMessages?: Message[]
  initialToolCalls?: ToolCall[]
}

export interface ConversationStream {
  messages: Message[]
  /** Все результаты Инструментов Диалога — рейка Evidence накапливает их подряд. */
  toolCalls: ToolCall[]
  streaming: boolean
  ask: (question: string) => void
  /** Повторить вопрос, ответ на который лежит в сообщении `agentMessageId`: неудачный
   * ответ заменяется новым, а вопрос в ленте не дублируется. */
  retry: (agentMessageId: string) => void
}

/** Состояние Диалога, собираемое из потока событий.
 *
 * Единственное место, которое знает, как события превращаются в сообщения: компоненты
 * получают готовую ленту, а транспорт — целиком за `stream`.
 */
export function useConversationStream({
  stream,
  initialMessages = [],
  initialToolCalls = [],
}: UseConversationStreamOptions): ConversationStream {
  const [messages, setMessages] = useState<Message[]>(initialMessages)
  const [toolCalls, setToolCalls] = useState<ToolCall[]>(initialToolCalls)
  const [streaming, setStreaming] = useState(false)

  // Номер прогона: новый вопрос и размонтирование обесценивают предыдущий поток, и
  // его события перестают применяться. Без этого ответ на отменённый вопрос дописывался
  // бы в ленту поверх нового.
  const runRef = useRef(0)
  // Отмена самого запроса, а не только его событий: без неё брошенный поток остаётся
  // висеть на чтении тела, и POST живёт до конца генерации на сервере.
  const abortRef = useRef<AbortController | null>(null)
  useEffect(
    () => () => {
      runRef.current += 1
      abortRef.current?.abort()
    },
    [],
  )

  const applyEvent = useCallback((event: StreamEvent, agentMessageId: string) => {
    if (event.type === 'tool_call') {
      const { toolCall } = event
      setToolCalls((previous) =>
        previous.some((existing) => existing.id === toolCall.id)
          ? previous
          : [...previous, toolCall],
      )
      return
    }

    setMessages((previous) =>
      previous.map((message) => {
        if (message.id !== agentMessageId || message.role !== 'agent') {
          return message
        }
        switch (event.type) {
          case 'token':
            return { ...message, text: message.text + event.text }
          case 'citation':
            // Один и тот же источник цитируется в ответе не по разу; в списке он нужен
            // однажды.
            return message.citations.some((existing) => existing.index === event.citation.index)
              ? message
              : { ...message, citations: [...message.citations, event.citation] }
          case 'error':
            return { ...message, streaming: false, error: event.problem }
          case 'done':
            return { ...message, streaming: false }
        }
      }),
    )
  }, [])

  const run = useCallback(
    async (question: string, { replaces }: { replaces?: string } = {}) => {
      runRef.current += 1
      const run = runRef.current
      abortRef.current?.abort()
      const abort = new AbortController()
      abortRef.current = abort
      const agentMessageId = `agent-${String(run)}`
      const agentMessage: AgentMessage = {
        id: agentMessageId,
        role: 'agent',
        text: '',
        citations: [],
        streaming: true,
      }

      setStreaming(true)
      setMessages((previous) =>
        replaces === undefined
          ? [...previous, { id: `user-${String(run)}`, role: 'user', text: question }, agentMessage]
          : // Повтор встаёт на место неудавшегося ответа: две попытки подряд читались
            // бы как два разных ответа Агента на один вопрос.
            previous.map((message) => (message.id === replaces ? agentMessage : message)),
      )

      try {
        let terminated = false
        for await (const event of stream(question, { signal: abort.signal })) {
          if (runRef.current !== run) {
            return
          }
          terminated = event.type === 'done' || event.type === 'error'
          applyEvent(event, agentMessageId)
        }
        if (!terminated && runRef.current === run) {
          // Поток закончился, не сказав ни `done`, ни `error` — так выглядит таймаут прокси
          // или упавший процесс. Без этой ветки курсор печати остался бы навсегда: лента
          // считала бы, что ответ ещё идёт, а повторить его было бы нечем.
          applyEvent(interrupted('The answer stream ended without finishing.'), agentMessageId)
        }
      } catch (cause) {
        // Транспорт может не дойти до события `error` — оборваться. Для ленты это тот
        // же исход: ошибка с объяснением и повтором, а не вечный курсор печати.
        if (runRef.current === run) {
          applyEvent(
            interrupted(
              cause instanceof Error
                ? cause.message
                : 'The connection to the Agent dropped mid-answer. Retry to ask again.',
            ),
            agentMessageId,
          )
        }
      } finally {
        if (runRef.current === run) {
          setStreaming(false)
        }
      }
    },
    [applyEvent, stream],
  )

  const ask = useCallback(
    (question: string) => {
      const trimmed = question.trim()
      if (trimmed === '' || streaming) {
        return
      }
      void run(trimmed)
    },
    [run, streaming],
  )

  const retry = useCallback(
    (agentMessageId: string) => {
      if (streaming) {
        return
      }
      // Вопрос берётся из реплики пользователя перед этим ответом, а не из «последнего
      // заданного»: повторяют конкретный ответ, а он может быть не последним в ленте.
      const at = messages.findIndex((message) => message.id === agentMessageId)
      const question = messages.slice(0, at).findLast((message) => message.role === 'user')
      if (at === -1 || question === undefined) {
        return
      }
      void run(question.text, { replaces: agentMessageId })
    },
    [messages, run, streaming],
  )

  return { messages, toolCalls, streaming, ask, retry }
}

/** Оборванный поток как событие ленты: у него тот же исход, что у события `error` от
 * бэкенда, — объяснение и кнопка повтора. */
function interrupted(detail: string): StreamEvent {
  return {
    type: 'error',
    problem: {
      type: 'urn:court-vision:problem:stream-interrupted',
      title: 'The answer stream stopped',
      status: 0,
      detail,
      instance: '/api/v1/conversations/messages',
    },
  }
}
