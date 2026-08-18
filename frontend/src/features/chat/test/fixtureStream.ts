import { findFixture } from '@/features/chat/test/fixtures'
import type { AnswerStream, StreamEvent } from '@/features/chat/model/types'

/** Постановочный поток ответа — тестовый дубль SSE.
 *
 * Приложение ходит в настоящий эндпоинт; здесь остаётся то, ради чего дубль и нужен:
 * последовательность событий, воспроизводимая без сети и без LLM. Формы событий — те же,
 * что у настоящего потока, поэтому механика ленты (курсор печати, цитаты, повтор,
 * обрыв) проверяется на них честно.
 *
 * Сначала Агент «вызывает» Инструменты (`tool_call`), затем отдаёт ответ токенами, а
 * цитата (`citation`) объявляется ровно в тот момент, когда в тексте встретился её
 * маркер. Заканчивается поток либо `done`, либо `error` — оборванным он не бывает.
 */

export interface MockStreamOptions {
  /** Пауза между событиями. Тесты подставляют мгновенную и не ждут таймеров. */
  sleep?: (ms: number) => Promise<void>
  toolDelayMs?: number
  tokenDelayMs?: number
}

const realSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

/** Маркеры цитат в тексте фикстуры: `…его карьеры[1][2].` — их в куске может быть
 * несколько, и объявить нужно каждый. */
const CITATION_MARKERS = /\[(\d+)]/g

export function createMockAnswerStream(options: MockStreamOptions = {}): AnswerStream {
  const { sleep = realSleep, toolDelayMs = 260, tokenDelayMs = 18 } = options

  return async function* mockAnswerStream(question: string): AsyncGenerator<StreamEvent> {
    const fixture = findFixture(question)

    if (fixture === undefined) {
      await sleep(toolDelayMs)
      yield {
        type: 'error',
        problem: {
          type: 'urn:court-vision:problem:fixture-missing',
          title: 'No scripted answer for this question',
          status: 501,
          // Ошибка говорит, что случилось и что делать (§7.2).
          detail:
            'This scripted stream only answers the questions it has fixtures for. Ask one of those, or retry.',
          instance: '/api/v1/conversations/messages',
        },
      }
      return
    }

    for (const toolCall of fixture.toolCalls) {
      await sleep(toolDelayMs)
      yield { type: 'tool_call', toolCall }
    }

    // Пробел остаётся при токене, иначе склеенный текст пришлось бы чинить в UI —
    // а настоящий поток тоже отдаёт куски текста, а не слова.
    for (const token of fixture.answer.split(/(?<=\s)/)) {
      await sleep(tokenDelayMs)
      yield { type: 'token', text: token }

      for (const marker of token.matchAll(CITATION_MARKERS)) {
        const index = Number(marker[1])
        const citation = fixture.citations.find((candidate) => candidate.index === index)
        if (citation !== undefined) {
          yield { type: 'citation', citation }
        }
      }
    }

    yield { type: 'done' }
  }
}

/** Поток по умолчанию: настоящие паузы, как их увидит пользователь. */
export const mockAnswerStream = createMockAnswerStream()
