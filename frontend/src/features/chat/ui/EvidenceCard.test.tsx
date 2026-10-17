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

  it('полосы перцентилей: подпись выборки, прочерк для не считавшейся метрики', () => {
    renderWithProviders(
      <EvidenceCard
        toolCall={{
          id: 'call_percentiles',
          name: 'get_player_percentiles',
          source: 'Stephen Curry · 2015-16',
          durationMs: 40,
          result: {
            kind: 'bars',
            caption: '2015-16 · vs 190 qualified players',
            bars: [
              { label: 'Pts', percentile: 99 },
              { label: '3P%', percentile: null },
            ],
          },
        }}
      />,
    )

    expect(screen.getByText('2015-16 · vs 190 qualified players')).toBeInTheDocument()
    expect(screen.getByText('99')).toBeInTheDocument()
    // Прочерк, а не «0»: ноль читался бы как худший Перцентиль.
    expect(screen.getByText('—')).toBeInTheDocument()
    expect(screen.queryByText('0')).not.toBeInTheDocument()
  })

  it('сравнение: игроки колонками, значение и перцентиль в ячейке, прочерк для не считавшейся', () => {
    renderWithProviders(
      <EvidenceCard
        toolCall={{
          id: 'call_compare',
          name: 'compare_players',
          source: 'Michael Jordan 1995-96, LeBron James 2012-13',
          durationMs: 88,
          result: {
            kind: 'comparison',
            players: [
              { name: 'Michael Jordan', season: '1995-96', qualified: true, sample_size: 190 },
              { name: 'LeBron James', season: '2012-13', qualified: false, sample_size: 220 },
            ],
            metrics: [
              {
                label: 'Pts',
                cells: [
                  { value: '30.4', percentile: 99 },
                  { value: '26.8', percentile: 97 },
                ],
              },
              {
                label: '3P%',
                cells: [
                  { value: '42.7', percentile: 91 },
                  { value: '—', percentile: null },
                ],
              },
            ],
          },
        }}
      />,
    )

    expect(screen.getByRole('columnheader', { name: 'Michael Jordan 1995-96' })).toBeVisible()
    expect(screen.getByRole('columnheader', { name: 'LeBron James 2012-13' })).toBeVisible()
    expect(screen.getByText('30.4 (99)')).toBeVisible()
    expect(screen.getByText('26.8 (97)')).toBeVisible()
    // Прочерк, а не «0 (0)»: ноль читался бы как худший Перцентиль.
    expect(screen.getByText('—', { selector: 'td' })).toBeVisible()
    // Неквалифицированный игрок назван в предупреждении.
    expect(screen.getByText(/Not qualified/)).toHaveTextContent('LeBron James 2012-13')
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

  it('похожие игроки — сезон, сходство и z-scores обоих по признакам сходства и различия', () => {
    renderWithProviders(
      <EvidenceCard
        toolCall={{
          ...CALLED,
          name: 'find_similar_players',
          durationMs: 31,
          result: {
            kind: 'similar',
            caption: 'Similar to Luka Doncic · 2025-26 · z-scores vs qualified players',
            items: [
              {
                name: 'James Harden',
                season: '2017-18',
                similarity: 0.969,
                matching: [{ label: 'Ast/36', target: 2.14, similar: 1.96 }],
                diverging: [{ label: 'Reb/36', target: 1.2, similar: -0.4 }],
              },
            ],
          },
        }}
      />,
    )

    expect(screen.getByText(/Similar to Luka Doncic/)).toBeInTheDocument()
    expect(screen.getByText('James Harden')).toBeInTheDocument()
    expect(screen.getByText('2017-18')).toBeInTheDocument()
    expect(screen.getByText('0.969')).toBeInTheDocument()
    expect(screen.getByText('Ast/36 +2.1 / +2.0')).toBeInTheDocument()
    // Минус — настоящий, не дефис: z-score ниже лиги читается рядом с плюсами.
    expect(screen.getByText('Reb/36 +1.2 / −0.4')).toBeInTheDocument()
  })

  it('отчёт показывает анонс со ссылкой на страницу отчёта', () => {
    renderWithProviders(
      <EvidenceCard
        toolCall={{
          ...CALLED,
          name: 'create_scouting_report',
          durationMs: 41200,
          result: {
            kind: 'report',
            items: [
              {
                report_id: '3f5c1a7e-0000-4000-8000-000000000001',
                player: 'Jalen Brunson',
                season: '2023-24',
                archetype: 'Primary scoring guard',
                summary: 'Brunson scored 28.7 points per game on strong efficiency.',
                url: '/reports/3f5c1a7e-0000-4000-8000-000000000001',
              },
            ],
          },
        }}
      />,
    )

    expect(screen.getByText('Jalen Brunson')).toBeInTheDocument()
    expect(screen.getByText('2023-24')).toBeInTheDocument()
    expect(screen.getByText('Primary scoring guard')).toBeInTheDocument()
    expect(screen.getByText(/28\.7 points per game/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open full report' })).toHaveAttribute(
      'href',
      '/reports/3f5c1a7e-0000-4000-8000-000000000001',
    )
    // Близость есть только у выдачи поиска — у созданного отчёта её нет.
    expect(screen.queryByText(/^\d\.\d{3}$/)).not.toBeInTheDocument()
  })

  it('выдача поиска по отчётам показывает близость каждого', () => {
    renderWithProviders(
      <EvidenceCard
        toolCall={{
          ...CALLED,
          name: 'search_reports',
          durationMs: 38,
          result: {
            kind: 'report',
            items: [
              {
                report_id: '3f5c1a7e-0000-4000-8000-000000000002',
                player: 'Jalen Brunson',
                season: '2022-23',
                archetype: 'Scoring guard',
                summary: 'Earlier look at Brunson.',
                url: '/reports/3f5c1a7e-0000-4000-8000-000000000002',
                similarity: 0.874,
              },
            ],
          },
        }}
      />,
    )

    expect(screen.getByText('0.874')).toBeInTheDocument()
  })
})
