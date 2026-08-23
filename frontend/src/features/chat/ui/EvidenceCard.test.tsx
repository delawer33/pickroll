import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { ToolCall } from '@/features/chat/model/types'
import { EvidenceCard } from '@/features/chat/ui/EvidenceCard'
import { renderWithProviders } from '@/test/render'

/** Три состояния одного вызова. Событий про вызов приходит два — `tool_call` до
 * выполнения Инструмента и `tool_result` после, — поэтому карточка обязана быть
 * читаемой и до результата, и на отказе, а не только на удачном вызове. */
const CALLED: ToolCall = {
  id: 'call_1',
  name: 'get_player_stats',
  source: 'LeBron James · 2012-13',
}

describe('EvidenceCard', () => {
  it('до результата показывает ожидание, а не пустоту и не «0 ms»', () => {
    renderWithProviders(<EvidenceCard toolCall={CALLED} />)

    expect(screen.getByText('get_player_stats')).toBeInTheDocument()
    expect(screen.getByText('LeBron James · 2012-13')).toBeInTheDocument()
    expect(screen.getByText('Running…')).toBeInTheDocument()
    expect(screen.queryByText(/ms$/)).not.toBeInTheDocument()
  })

  it('с результатом показывает то, что Инструмент принёс, и длительность', () => {
    renderWithProviders(
      <EvidenceCard
        toolCall={{
          ...CALLED,
          durationMs: 174,
          result: {
            kind: 'table',
            columns: ['Season', 'GP', 'Pts'],
            rows: [['2012-13', '76', '26.8']],
          },
        }}
      />,
    )

    expect(screen.getByText('174 ms')).toBeInTheDocument()
    expect(screen.getByText('26.8')).toBeInTheDocument()
    expect(screen.queryByText('Running…')).not.toBeInTheDocument()
  })

  it('отработавший вызов без проекции не выдаёт себя за идущий', () => {
    // Длительность есть, показывать нечего — «Running…» рядом с «12 ms» противоречит сам
    // себе, а именно так и читался бы вызов Инструмента, у которого проекции нет.
    renderWithProviders(<EvidenceCard toolCall={{ ...CALLED, durationMs: 12 }} />)

    expect(screen.getByText('12 ms')).toBeInTheDocument()
    expect(screen.queryByText('Running…')).not.toBeInTheDocument()
    expect(screen.getByText('Nothing to show for this result.')).toBeInTheDocument()
  })

  it('выдача поиска по корпусу — цитаты с номерами [n] и mono-локаторами', () => {
    renderWithProviders(
      <EvidenceCard
        toolCall={{
          id: 'call_corpus',
          name: 'search_corpus',
          source: 'goaltending rule',
          durationMs: 148,
          result: {
            kind: 'quotes',
            quotes: [
              {
                index: 1,
                title: 'NBA Official Playing Rules',
                locator: 'Rule 11 — Sec. I',
                text: 'Goaltending is touching the ball on its downward flight.',
              },
              {
                index: 2,
                title: 'NBA Official Playing Rules',
                locator: 'Rule 11 — Sec. II',
                text: 'The ball may not be touched while it is in the cylinder.',
              },
            ],
          },
        }}
      />,
    )

    // Номер цитаты — тот же `[n]`, что у маркера в ответе: по нему пара «маркер-цитата»
    // и находится глазами.
    expect(screen.getByText('[1]')).toBeInTheDocument()
    expect(screen.getByText('[2]')).toBeInTheDocument()
    expect(screen.getByText('NBA Official Playing Rules · Rule 11 — Sec. I')).toBeInTheDocument()
    expect(
      screen.getByText('Goaltending is touching the ball on its downward flight.'),
    ).toBeInTheDocument()
  })

  it('отказ Инструмента подписан причиной и не выглядит удачным вызовом', () => {
    // Карточка вызова без данных обязана отличаться от карточки с данными: иначе рейка
    // говорит «Агент сходил за данными» там, где Агент вернулся ни с чем.
    renderWithProviders(
      <EvidenceCard
        toolCall={{
          ...CALLED,
          durationMs: 12,
          error: {
            kind: 'season-data-missing',
            detail: 'Данных за сезон 2005-06 в базе нет.',
          },
        }}
      />,
    )

    expect(screen.getByText('season-data-missing')).toBeInTheDocument()
    expect(screen.getByText('Данных за сезон 2005-06 в базе нет.')).toBeInTheDocument()
  })
})
