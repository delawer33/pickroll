import { describe, expect, it } from 'vitest'

import { splitAnswer } from '@/features/chat/model/splitAnswer'
import type { Citation } from '@/features/chat/model/types'

function citation(index: number, toolCallId: string): Citation {
  return {
    index,
    toolCallId,
    chunkId: index,
    documentTitle: 'NBA Official Playing Rules',
    locator: `Rule ${String(index)}`,
    snippet: 'Goaltending is touching the ball on its downward flight.',
  }
}

describe('splitAnswer', () => {
  it('разбирает маркер цитаты и связывает его с вызовом Инструмента', () => {
    const parts = splitAnswer('Assists rose to 15.9[1]. The pass got earlier[2].', [
      citation(1, 'call_stats'),
      citation(2, 'call_corpus'),
    ])

    expect(parts).toEqual([
      { kind: 'text', text: 'Assists rose to 15.9' },
      { kind: 'citation', index: 1, toolCallId: 'call_stats' },
      { kind: 'text', text: '. The pass got earlier' },
      { kind: 'citation', index: 2, toolCallId: 'call_corpus' },
      { kind: 'text', text: '.' },
    ])
  })

  it('показывает маркер и до прихода события citation, но без ссылки', () => {
    // Во время стриминга токен с маркером приходит раньше самого события.
    const parts = splitAnswer('Best ratio of his career[1]', [])

    expect(parts).toEqual([
      { kind: 'text', text: 'Best ratio of his career' },
      { kind: 'citation', index: 1 },
    ])
  })

  it('текст без маркеров остаётся одной частью', () => {
    expect(splitAnswer('Volume is up.', [])).toEqual([{ kind: 'text', text: 'Volume is up.' }])
  })
})
