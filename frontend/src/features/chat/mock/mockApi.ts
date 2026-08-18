import {
  CONVERSATION_QUESTIONS,
  MOCK_CONVERSATIONS,
  findFixture,
  type ConversationSummary,
} from '@/features/chat/mock/fixtures'
import type { Message, ToolCall } from '@/features/chat/model/types'
import { ApiError } from '@/shared/api/client'

/** Мок чтения Диалогов: то, что в #8 станет `GET /api/v1/conversations` и
 * `GET /api/v1/conversations/{id}`. Задержка настоящая — иначе состояние загрузки
 * невозможно ни увидеть, ни проверить. */

export interface ConversationDetail {
  id: string
  title: string
  messages: Message[]
  toolCalls: ToolCall[]
}

const LATENCY_MS = 420

const delay = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

export async function listConversations(): Promise<ConversationSummary[]> {
  await delay(LATENCY_MS)
  return MOCK_CONVERSATIONS
}

export async function fetchConversation(id: string): Promise<ConversationDetail> {
  await delay(LATENCY_MS)

  const summary = MOCK_CONVERSATIONS.find((conversation) => conversation.id === id)
  const question = CONVERSATION_QUESTIONS[id]
  const fixture = question === undefined ? undefined : findFixture(question)

  if (summary === undefined || question === undefined || fixture === undefined) {
    throw new ApiError({
      type: 'urn:court-vision:problem:conversation-not-found',
      title: 'Conversation not found',
      status: 404,
      detail: `No conversation with id ${id}. It may belong to another account, or the link is stale.`,
      instance: `/api/v1/conversations/${id}`,
    })
  }

  return {
    id,
    title: summary.title,
    toolCalls: fixture.toolCalls,
    messages: [
      { id: `${id}-user`, role: 'user', text: question },
      {
        id: `${id}-agent`,
        role: 'agent',
        text: fixture.answer,
        citations: fixture.citations,
        streaming: false,
      },
    ],
  }
}
