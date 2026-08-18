/** Разбор потока Server-Sent Events.
 *
 * Свой парсер, а не `EventSource`: тот не умеет ни POST, ни заголовок `Authorization`, а
 * запрос к Диалогу — это POST с Bearer-токеном Clerk. Остаётся `fetch` и разбор тела
 * руками, зато ровно по спецификации: фреймы разделены пустой строкой, `data` может быть
 * многострочной, строка с двоеточия в начале — комментарий (им же приходит keepalive).
 */

/** Потолок незакрытого фрейма. Поток, в котором пустая строка не приходит вовсе (прокси
 * переписал разделители, гигантский фрейм), иначе растил бы буфер до падения вкладки —
 * лучше видимая ошибка, чем зависшая страница. */
const MAX_FRAME_BYTES = 1024 * 1024

/** Полезная нагрузка `data` каждого фрейма, по мере поступления. */
export async function* sseFrames(body: ReadableStream<Uint8Array>): AsyncGenerator<string> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) {
        // Флеш декодера: на обрыве посреди многобайтового символа его хвост иначе остался
        // бы внутри декодера, и последний фрейм потерялся бы молча.
        buffer += decoder.decode()
        break
      }
      // stream: true — многобайтовый символ может быть разрезан границей чанка.
      buffer += decoder.decode(value, { stream: true })
      if (buffer.length > MAX_FRAME_BYTES) {
        throw new Error('SSE: фрейм не закрывается — поток похож на что угодно, кроме SSE.')
      }

      let boundary = nextBoundary(buffer)
      while (boundary !== null) {
        const frame = buffer.slice(0, boundary.at)
        buffer = buffer.slice(boundary.at + boundary.length)
        const data = frameData(frame)
        if (data !== null) {
          yield data
        }
        boundary = nextBoundary(buffer)
      }
    }

    // Последний фрейм мог прийти без завершающей пустой строки.
    const data = frameData(buffer)
    if (data !== null) {
      yield data
    }
  } finally {
    // Отпускаем тело, даже если поток бросили на середине: иначе соединение висит до
    // сборки мусора, а на уходе со страницы это ровно тот случай.
    reader.cancel().catch(() => undefined)
  }
}

/** Разобранные `data` как JSON. Фрейм, который не разбирается, пропускается: обрывок
 * чужого формата не повод убивать поток, который дальше может быть в порядке. */
export async function* sseJson<EventT>(body: ReadableStream<Uint8Array>): AsyncGenerator<EventT> {
  for await (const data of sseFrames(body)) {
    let parsed: unknown
    try {
      parsed = JSON.parse(data)
    } catch {
      continue
    }
    yield parsed as EventT
  }
}

/** Граница фрейма — пустая строка. Перевод строки бывает и `\n`, и `\r\n`. */
function nextBoundary(buffer: string): { at: number; length: number } | null {
  const lf = buffer.indexOf('\n\n')
  const crlf = buffer.indexOf('\r\n\r\n')
  if (crlf !== -1 && (lf === -1 || crlf < lf)) {
    return { at: crlf, length: 4 }
  }
  return lf === -1 ? null : { at: lf, length: 2 }
}

/** `null`, если во фрейме нет ни одной строки `data` — например у keepalive-комментария. */
function frameData(frame: string): string | null {
  const lines = frame.split(/\r\n|\n/)
  const data = lines
    .filter((line) => line.startsWith('data:'))
    // По спецификации отбрасывается один пробел после двоеточия, остальные значимы.
    .map((line) => line.slice('data:'.length).replace(/^ /, ''))
  return data.length === 0 ? null : data.join('\n')
}
