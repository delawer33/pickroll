import { describe, expect, it } from 'vitest'

import { formatWhen } from '@/features/chat/model/formatWhen'

const NOW = new Date('2026-08-18T12:00:00Z')

describe('formatWhen', () => {
  it('свежий Диалог — минуты, не «0 дней»', () => {
    expect(formatWhen('2026-08-18T11:40:00Z', NOW)).toBe('20 minutes ago')
  })

  it('вчерашний Диалог называется вчерашним', () => {
    // numeric: 'auto' — ради «yesterday» вместо «1 day ago».
    expect(formatWhen('2026-08-17T12:00:00Z', NOW)).toBe('yesterday')
  })

  it('крупная единица выбирается по величине разрыва', () => {
    expect(formatWhen('2026-06-18T12:00:00Z', NOW)).toBe('2 months ago')
  })

  it('только что созданный Диалог не даёт пустой подписи', () => {
    expect(formatWhen('2026-08-18T11:59:59Z', NOW)).toBe('this minute')
  })

  it('битую метку показывает как есть, а не прячет', () => {
    // Пустая подпись читалась бы как «неизвестно когда» — а это ошибка данных.
    expect(formatWhen('не дата', NOW)).toBe('не дата')
  })
})
