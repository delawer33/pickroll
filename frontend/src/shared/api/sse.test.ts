import { describe, expect, it } from 'vitest'

import { sseFrames, sseJson } from '@/shared/api/sse'

/** Тело потока из заданных куска: границы чанков намеренно не совпадают с границами
 * фреймов — именно так их и режет сеть. */
function body(...chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk))
      }
      controller.close()
    },
  })
}

async function collect<T>(source: AsyncIterable<T>): Promise<T[]> {
  const items: T[] = []
  for await (const item of source) {
    items.push(item)
  }
  return items
}

describe('sseFrames', () => {
  it('разбирает фреймы, разделённые пустой строкой', async () => {
    const frames = await collect(sseFrames(body('data: one\r\n\r\ndata: two\r\n\r\n')))

    expect(frames).toEqual(['one', 'two'])
  })

  it('собирает фрейм, разрезанный границей чанка', async () => {
    const frames = await collect(sseFrames(body('data: {"type":"to', 'ken"}\n\n')))

    expect(frames).toEqual(['{"type":"token"}'])
  })

  it('пропускает keepalive-комментарий, а не отдаёт его событием', async () => {
    const frames = await collect(sseFrames(body(': ping\r\n\r\ndata: real\r\n\r\n')))

    expect(frames).toEqual(['real'])
  })

  it('отдаёт последний фрейм без завершающей пустой строки', async () => {
    // Сервер закрыл соединение сразу после события — терять его нельзя, это может быть `done`.
    const frames = await collect(sseFrames(body('data: last')))

    expect(frames).toEqual(['last'])
  })

  it('склеивает многострочную data через перевод строки, как велит спецификация', async () => {
    const frames = await collect(sseFrames(body('data: first\ndata: second\n\n')))

    expect(frames).toEqual(['first\nsecond'])
  })

  it('не съедает значимые пробелы, кроме одного после двоеточия', async () => {
    const frames = await collect(sseFrames(body('data:  двойной\n\n')))

    expect(frames).toEqual([' двойной'])
  })
})

describe('sseJson', () => {
  it('отдаёт разобранные события', async () => {
    const events = await collect(
      sseJson<{ type: string }>(body('data: {"type":"token"}\n\ndata: {"type":"done"}\n\n')),
    )

    expect(events).toEqual([{ type: 'token' }, { type: 'done' }])
  })

  it('пропускает нечитаемый фрейм и продолжает поток', async () => {
    // Обрывок чужого формата — не повод убить ответ, который дальше в порядке.
    const events = await collect(
      sseJson<{ type: string }>(body('data: {\n\ndata: {"type":"done"}\n\n')),
    )

    expect(events).toEqual([{ type: 'done' }])
  })
})
