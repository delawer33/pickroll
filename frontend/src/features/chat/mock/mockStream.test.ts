import { describe, expect, it } from 'vitest'

import { createMockAnswerStream } from '@/features/chat/mock/mockStream'
import type { StreamEvent } from '@/features/chat/model/types'

const stream = createMockAnswerStream({ sleep: () => Promise.resolve() })

async function collect(question: string): Promise<StreamEvent[]> {
  const events: StreamEvent[] = []
  for await (const event of stream(question)) {
    events.push(event)
  }
  return events
}

describe('createMockAnswerStream', () => {
  it('отдаёт вызовы Инструментов до текста и заканчивает поток событием done', async () => {
    const events = await collect('How has Jokić’s passing changed since 2023?')

    const kinds = events.map((event) => event.type)
    expect(kinds.at(-1)).toBe('done')
    expect(kinds.indexOf('tool_call')).toBe(0)
    expect(kinds.lastIndexOf('tool_call')).toBeLessThan(kinds.indexOf('token'))
  })

  it('склеенные токены дают полный ответ фикстуры', async () => {
    const events = await collect('lebron 2012-13')
    const text = events
      .filter((event) => event.type === 'token')
      .map((event) => event.text)
      .join('')

    expect(text).toContain('26.8 points')
    expect(text.startsWith('LeBron averaged')).toBe(true)
  })

  it('объявляет цитату сразу после токена с её маркером', async () => {
    const events = await collect('jokic')
    const citationAt = events.findIndex((event) => event.type === 'citation')
    const previous = events[citationAt - 1]

    expect(citationAt).toBeGreaterThan(0)
    expect(previous?.type).toBe('token')
    expect(previous?.type === 'token' && previous.text).toContain('[1]')
  })

  it('вопрос без фикстуры заканчивается ошибкой, а не обрывом потока', async () => {
    const events = await collect('Who won the 1971 finals?')

    expect(events).toHaveLength(1)
    expect(events[0]?.type).toBe('error')
    expect(events[0]?.type === 'error' && events[0].problem.status).toBe(501)
  })
})
