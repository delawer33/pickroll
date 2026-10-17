import { RouterProvider, createMemoryHistory, createRouter } from '@tanstack/react-router'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { PropsWithChildren } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { routeTree } from '@/routeTree.gen'
import {
  REPORT_ID,
  REPORT_STREAM_EVENTS,
  SAVED_REPORT_SUMMARY,
  installApiStub,
} from '@/test/apiStub'
import { renderWithProviders } from '@/test/render'
import type { ReportStreamEvent, ReportSummary } from '@/shared/api/wire'

vi.mock('@clerk/clerk-react', () => ({
  ClerkLoading: () => null,
  SignedIn: ({ children }: PropsWithChildren) => children,
  SignedOut: () => null,
  RedirectToSignIn: () => null,
  SignIn: () => <div data-testid="clerk-sign-in" />,
  UserButton: () => <div data-testid="clerk-user-button" />,
  useAuth: () => ({ getToken: () => Promise.resolve('session-jwt') }),
}))

function renderApp(initialPath: string) {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [initialPath] }),
  })
  const user = userEvent.setup()
  renderWithProviders(<RouterProvider router={router} />)
  return { router, user }
}

function summary(id: number, name: string, playerId: number): ReportSummary {
  return {
    ...SAVED_REPORT_SUMMARY,
    report_id: `00000000-0000-4000-8000-${String(id).padStart(12, '0')}`,
    player: { player_id: playerId, full_name: name },
    summary: `${name} summary ${String(id)}`,
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('список отчётов', () => {
  it('показывает свои отчёты и открывает выбранный', async () => {
    installApiStub()
    const { router, user } = renderApp('/reports')

    // Первый тест файла грузит чанки роутов — запас по времени выше дефолтной секунды.
    const link = await screen.findByRole('link', { name: /Jalen Brunson/ }, { timeout: 4000 })
    expect(within(link).getByText(/Scoring lead guard/)).toBeInTheDocument()

    await user.click(link)

    expect(router.state.location.pathname).toBe(`/reports/${REPORT_ID}`)
    expect(await screen.findByRole('heading', { name: 'Jalen Brunson', level: 1 })).toBeVisible()
  })

  it('пункт навигации ведёт на список', async () => {
    installApiStub()
    const { router, user } = renderApp('/chat')

    const rail = await screen.findByRole('complementary', { name: 'Navigation' })
    await user.click(within(rail).getByRole('link', { name: 'Scouting reports' }))

    expect(router.state.location.pathname).toBe('/reports')
  })

  it('фильтр по игроку запрашивает только его отчёты и живёт в адресе', async () => {
    const api = installApiStub({
      reports: [summary(1, 'Jalen Brunson', 10), summary(2, 'Luka Doncic', 20)],
    })
    const { router, user } = renderApp('/reports')

    expect(await screen.findByText('Luka Doncic summary 2')).toBeInTheDocument()
    const select = screen.getByLabelText('Player')
    await user.selectOptions(select, await screen.findByRole('option', { name: 'Luka Doncic' }))

    expect(router.state.location.search).toEqual({ player: 20 })
    expect(await screen.findByText('Luka Doncic summary 2')).toBeInTheDocument()
    expect(screen.queryByText('Jalen Brunson summary 1')).not.toBeInTheDocument()
    expect(api.requests.some((call) => call.includes('player_id=20'))).toBe(true)
  })

  it('открытая со фильтром ссылка применяет его сразу', async () => {
    const api = installApiStub({
      reports: [summary(1, 'Jalen Brunson', 10), summary(2, 'Luka Doncic', 20)],
    })
    renderApp('/reports?player=10')

    expect(await screen.findByText('Jalen Brunson summary 1')).toBeInTheDocument()
    expect(screen.queryByText('Luka Doncic summary 2')).not.toBeInTheDocument()
    expect(api.requests.some((call) => call.includes('player_id=10'))).toBe(true)
  })

  it('дочитывает следующую страницу по курсору', async () => {
    const many = Array.from({ length: 25 }, (_, index) => summary(index + 1, 'Jalen Brunson', 10))
    const api = installApiStub({ reports: many })
    const { user } = renderApp('/reports')

    expect(await screen.findByText('Jalen Brunson summary 20')).toBeInTheDocument()
    expect(screen.queryByText('Jalen Brunson summary 21')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Load more' }))

    expect(await screen.findByText('Jalen Brunson summary 25')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
    expect(api.requests.some((call) => call.includes('cursor=20'))).toBe(true)
  })

  it('пустой список предлагает создать первый отчёт', async () => {
    installApiStub({ reports: [] })
    renderApp('/reports')

    expect(await screen.findByText('No scouting reports yet.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Generate report' })).toHaveAttribute(
      'href',
      '/reports/new',
    )
  })
})

describe('страница отчёта', () => {
  it('рисует все разделы сохранённого отчёта', async () => {
    installApiStub()
    renderApp(`/reports/${REPORT_ID}`)

    expect(await screen.findByRole('heading', { name: 'Jalen Brunson', level: 1 })).toBeVisible()
    for (const section of ['Summary', 'Strengths', 'Weaknesses', 'Statistical profile']) {
      expect(screen.getByRole('heading', { name: section })).toBeInTheDocument()
    }
    expect(screen.getByRole('heading', { name: 'Similar players' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Outlook' })).toBeInTheDocument()
    expect(screen.getByText('Efficient scoring on high volume')).toBeInTheDocument()
    expect(screen.getByText('ts_pct')).toBeInTheDocument()
    expect(screen.getByText('Kyrie Irving')).toBeInTheDocument()
  })

  it('чужой или несуществующий отчёт — понятная ошибка, а не пустая страница', async () => {
    installApiStub({ report: null })
    renderApp(`/reports/${REPORT_ID}`)

    expect(await screen.findByRole('alert')).toHaveTextContent('Report not found')
  })
})

describe('новый отчёт', () => {
  async function order(user: ReturnType<typeof userEvent.setup>, player = 'Jalen Brunson') {
    await user.type(await screen.findByLabelText('Player'), player)
    await user.click(screen.getByRole('button', { name: 'Generate report' }))
  }

  it('стримит разделы и по done уходит на страницу отчёта', async () => {
    const api = installApiStub()
    const { router, user } = renderApp('/reports/new')

    await order(user)

    expect(router.state.location.pathname).toBe(`/reports/${REPORT_ID}`)
    expect(await screen.findByRole('heading', { name: 'Jalen Brunson', level: 1 })).toBeVisible()
    expect(api.calls).toContain('POST /api/v1/reports')
  })

  it('показывает разделы по мере прихода снапшотов, остальные — скелетонами', async () => {
    // Поток без `done` и не закрытый: страница стоит на последнем снапшоте.
    const partial = REPORT_STREAM_EVENTS.filter((event) => event.type === 'snapshot')
    installApiStub({ reportStreams: [partial], reportStreamHangs: true })
    const { user } = renderApp('/reports/new')

    await order(user)

    expect(await screen.findByText('Brunson is an elite pull-up creator.')).toBeInTheDocument()
    expect(screen.getByText('Efficient scoring on high volume')).toBeInTheDocument()
    expect(screen.getAllByRole('status', { name: 'Writing section' }).length).toBeGreaterThan(0)
  })

  it('передаёт сезон, если он указан', async () => {
    const api = installApiStub()
    const fetchSpy = vi.fn(globalThis.fetch)
    vi.stubGlobal('fetch', fetchSpy)
    const { user } = renderApp('/reports/new')

    await user.type(await screen.findByLabelText('Player'), 'Jalen Brunson')
    await user.type(screen.getByLabelText('Season (optional)'), '2022-23')
    await user.click(screen.getByRole('button', { name: 'Generate report' }))

    await screen.findByRole('heading', { name: 'Jalen Brunson', level: 1 })
    const post = fetchSpy.mock.calls.find(([, init]) => init?.method === 'POST')
    expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({
      player_name: 'Jalen Brunson',
      season: '2022-23',
    })
    expect(api.calls.length).toBeGreaterThan(0)
  })

  it('не отправляет сезон в неверном формате', async () => {
    installApiStub()
    const { user } = renderApp('/reports/new')

    await user.type(await screen.findByLabelText('Player'), 'Jalen Brunson')
    await user.type(screen.getByLabelText('Season (optional)'), '2023')

    expect(screen.getByRole('button', { name: 'Generate report' })).toBeDisabled()
    expect(screen.getByText('Use the format 2023-24.')).toBeInTheDocument()
  })

  it('error вместо done — ошибка с повтором, повтор доводит до отчёта', async () => {
    const failed: ReportStreamEvent[] = [
      { type: 'snapshot', report: { summary: 'Half a report' } },
      {
        type: 'error',
        problem: {
          type: 'urn:court-vision:problem:report-generation-failed',
          title: 'Report generation failed',
          status: 502,
          detail: 'The model broke the schema twice. Retry.',
          instance: '/api/v1/reports',
        },
      },
    ]
    const api = installApiStub({ reportStreams: [failed, REPORT_STREAM_EVENTS] })
    const { router, user } = renderApp('/reports/new')

    await order(user)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Report generation failed')
    expect(alert).toHaveTextContent('The model broke the schema twice. Retry.')
    // Недописанный отчёт не выдаётся за результат.
    expect(screen.queryByText('Half a report')).not.toBeInTheDocument()

    await user.click(within(alert).getByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('heading', { name: 'Jalen Brunson', level: 1 })).toBeVisible()
    expect(router.state.location.pathname).toBe(`/reports/${REPORT_ID}`)
    expect(api.calls.filter((call) => call === 'POST /api/v1/reports')).toHaveLength(2)
  })

  it('поток закрылся без done и error — обрыв с повтором, а не вечная генерация', async () => {
    const dropped: ReportStreamEvent[] = [
      { type: 'snapshot', report: { summary: 'Half a report' } },
    ]
    installApiStub({ reportStreams: [dropped] })
    const { user } = renderApp('/reports/new')

    await order(user)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Report stream interrupted')
    expect(within(alert).getByRole('button', { name: 'Retry' })).toBeEnabled()
  })

  it('отказ до потока (неизвестный игрок) показывает Problem Details', async () => {
    installApiStub({ reportOrderRejected: true })
    const { user } = renderApp('/reports/new')

    await order(user, 'Nobody Real')

    expect(await screen.findByRole('alert')).toHaveTextContent('Player not found')
  })
})
