import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { SeriesResult } from '@/features/chat/ui/SeriesResult'
import { renderWithProviders } from '@/test/render'

const SERIES = [
  {
    name: 'Curry',
    points: [
      { x: '2023-24', y: 11.8 },
      { x: '2024-25', y: 9.9 },
    ],
  },
  {
    name: 'Lillard',
    points: [
      { x: '2023-24', y: 8.4 },
      { x: '2024-25', y: 8.6 },
    ],
  },
]

function renderSeries() {
  const user = userEvent.setup()
  renderWithProviders(
    <SeriesResult metric="Three-point attempts" unit="per game" series={SERIES} />,
  )
  return { user }
}

describe('SeriesResult', () => {
  it('открывается графиком и называет показатель с единицей', () => {
    renderSeries()

    expect(screen.getByText(/Three-point attempts/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Chart' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('переключается на таблицу — обязательный доступный путь к тем же числам', async () => {
    const { user } = renderSeries()

    await user.click(screen.getByRole('button', { name: 'Table' }))

    const table = screen.getByRole('table')
    expect(table).toHaveAccessibleName('Three-point attempts, per game')
    // Колонка на серию плюс сезон, строка на точку.
    expect(screen.getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual([
      'Season',
      'Curry',
      'Lillard',
    ])
    expect(screen.getByRole('row', { name: '2024-25 9.9 8.6' })).toBeInTheDocument()
  })
})
