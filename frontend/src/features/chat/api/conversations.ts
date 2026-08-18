/** Чтение и создание Диалогов через API.
 *
 * Здесь же перевод wire-формата в модель ленты: у API роль называется `assistant`, а в
 * ленте — `agent`, и идентификаторы Реплик числовые. Компоненты про wire-формат не знают.
 */

import type { Message } from '@/features/chat/model/types'
import type { ApiClient } from '@/shared/api/client'
import type { ConversationSummary, MessageResponse, Page } from '@/shared/api/wire'

const CONVERSATIONS = '/api/v1/conversations'

/** Потолок страницы на бэкенде. Берём максимум: Диалог открывают целиком. */
const PAGE_SIZE = 100

/** Сколько Диалогов показывает рейка. Ровно одна страница, и это осознанно: рейка — это
 * «недавние», а не «все». Дочитывать курсор до конца ради списка, который всё равно не
 * пролистать, значит тянуть тысячу заголовков на каждый заход на страницу; поиск и полная
 * история — отдельный экран (спринт 3), там и понадобится пагинация. */
const RECENT_LIMIT = 100

export interface ConversationListItem {
  id: string
  title: string
  createdAt: string
}

/** Недавние Диалоги для рейки — первая страница, без обхода курсора (см. `RECENT_LIMIT`). */
export async function listConversations(client: ApiClient): Promise<ConversationListItem[]> {
  const page = await client.get<Page<ConversationSummary>>(`${CONVERSATIONS}?limit=${RECENT_LIMIT}`)
  return page.items.map((item) => ({
    id: item.conversation_id,
    title: item.title,
    createdAt: item.created_at,
  }))
}

/** Заводит Диалог. Без заголовка — его подставит первый вопрос на бэкенде, поэтому
 * придумывать его на клиенте (и расходиться с сервером) не нужно. */
export async function createConversation(client: ApiClient): Promise<string> {
  const created = await client.post<ConversationSummary>(CONVERSATIONS, { title: null })
  return created.conversation_id
}

/** Сохранённые Реплики Диалога, все страницы.
 *
 * Страницы дочитываются до конца: Диалог читают сверху вниз целиком, и «первые сто
 * Реплик» — это не то, что открывал пользователь. Курсорная пагинация гарантирует
 * продвижение, поэтому цикл конечен.
 */
export async function fetchMessages(client: ApiClient, conversationId: string): Promise<Message[]> {
  const messages: Message[] = []
  let cursor: string | null = null

  do {
    const query = new URLSearchParams({ limit: String(PAGE_SIZE) })
    if (cursor !== null) {
      query.set('cursor', cursor)
    }
    const page: Page<MessageResponse> = await client.get<Page<MessageResponse>>(
      `${CONVERSATIONS}/${conversationId}/messages?${query.toString()}`,
    )
    messages.push(...page.items.map(toMessage))
    cursor = page.next_cursor
  } while (cursor !== null)

  return messages
}

function toMessage(message: MessageResponse): Message {
  const id = `message-${String(message.message_id)}`
  return message.role === 'user'
    ? { id, role: 'user', text: message.content }
    : // Сохранённая Реплика Агента дочитана до конца: курсора печати под ней нет, а цитаты
      // приедут в спринте 2 вместе с корпусом.
      { id, role: 'agent', text: message.content, citations: [], streaming: false }
}
