import type { Citation } from '@/features/chat/model/types'

/** Часть ответа: либо текст, либо маркер цитаты. */
export type AnswerPart =
  | { kind: 'text'; text: string }
  | { kind: 'citation'; index: number; toolCallId?: string }

const CITATION_MARKER = /\[(\d+)]/g

/** Разбирает ответ на текст и маркеры цитат.
 *
 * Маркер показывается сразу, даже если событие `citation` ещё не пришло: во время
 * стриминга оно приходит следующим шагом, и прятать надстрочный номер на кадр — значит
 * дёргать строку. Кликабельным маркер становится, только когда известно, на какую
 * карточку Evidence он указывает.
 */
export function splitAnswer(text: string, citations: Citation[]): AnswerPart[] {
  const parts: AnswerPart[] = []
  let cursor = 0

  for (const match of text.matchAll(CITATION_MARKER)) {
    const at = match.index
    if (at > cursor) {
      parts.push({ kind: 'text', text: text.slice(cursor, at) })
    }
    const index = Number(match[1])
    const citation = citations.find((candidate) => candidate.index === index)
    parts.push(
      citation === undefined
        ? { kind: 'citation', index }
        : { kind: 'citation', index, toolCallId: citation.toolCallId },
    )
    cursor = at + match[0].length
  }

  if (cursor < text.length) {
    parts.push({ kind: 'text', text: text.slice(cursor) })
  }
  return parts
}
