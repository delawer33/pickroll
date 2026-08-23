import { RouterProvider, createMemoryHistory, createRouter } from '@tanstack/react-router'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { PropsWithChildren } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { routeTree } from '@/routeTree.gen'
import { renderWithProviders } from '@/test/render'
import { CONVERSATION_ID, installApiStub, type ApiStub } from '@/test/apiStub'

// Clerk мокается целиком: его компоненты тянут clerk-js по сети. Мок повторяет
// контракт — рендерить детей по состоянию сессии и отдавать токен; здесь сессия всегда есть.
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

describe('роутинг приложения', () => {
  let api: ApiStub

  beforeEach(() => {
    api = installApiStub()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('корень ведёт на новый Диалог', async () => {
    const { router } = renderApp('/')

    expect(await screen.findByText(/Ask about a player/)).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/chat')
  })

  it('рейка показывает историю Диалогов из API и открывает выбранный', async () => {
    const { router, user } = renderApp('/chat')

    const rail = await screen.findByRole('complementary', { name: 'Navigation' })
    await user.click(await within(rail).findByRole('link', { name: /Jokić’s passing/ }))

    expect(router.state.location.pathname).toBe(`/chat/${CONVERSATION_ID}`)
    // Сначала скелетон в форме контента, затем сохранённая лента.
    expect(await screen.findByRole('status', { name: 'Loading conversation' })).toBeInTheDocument()
    expect(await screen.findByText(/Volume is up/)).toBeInTheDocument()
    expect(screen.getByText('How has Jokić’s passing changed since 2023?')).toBeInTheDocument()
    // Запросы ушли с токеном Clerk: без него бэкенд ответил бы 401.
    expect(api.lastAuthorization).toBe('Bearer session-jwt')
  })

  it('вопрос в новом Диалоге создаёт его и стримит ответ', async () => {
    const { user } = renderApp('/chat')

    await user.click(await screen.findByRole('button', { name: /How many points did LeBron/ }))

    expect(await screen.findByText(/LeBron averaged/)).toBeInTheDocument()
    // Диалог заведён ровно один раз и именно вопросом, а не заходом на страницу.
    expect(api.calls.filter((call) => call === 'POST /api/v1/conversations')).toHaveLength(1)
    expect(api.calls).toContain(`POST /api/v1/conversations/${CONVERSATION_ID}/messages`)
    // Появившийся Диалог обновил историю в рейке: список перезапрошен.
    expect(api.calls.filter((call) => call === 'GET /api/v1/conversations').length).toBeGreaterThan(
      1,
    )

    // Инструмент, за которым Агент пошёл, виден в рейке Evidence вместе с аргументами.
    const rail = screen.getByRole('complementary', { name: 'Evidence' })
    expect(within(rail).getByText('get_player_stats')).toBeInTheDocument()
    // Штамп источника — аргументы вызова: по ним запрос можно повторить руками.
    expect(within(rail).getByText('LeBron James · 2012-13')).toBeInTheDocument()
  })

  it('рейка сворачивается и оставляет только иконки', async () => {
    const { user } = renderApp('/chat')

    const rail = await screen.findByRole('complementary', { name: 'Navigation' })
    await user.click(within(rail).getByRole('button', { name: 'Collapse sidebar' }))

    const expand = within(rail).getByRole('button', { name: 'Expand sidebar' })
    expect(expand).toHaveAttribute('aria-pressed', 'true')
    expect(rail).toHaveClass('w-rail-collapsed')
  })

  it('«New conversation» начинает ленту заново, а не продолжает старую', async () => {
    const { user } = renderApp('/chat')

    await user.click(await screen.findByRole('button', { name: /How many points did LeBron/ }))
    expect(await screen.findByText(/LeBron averaged/)).toBeInTheDocument()

    const rail = screen.getByRole('complementary', { name: 'Navigation' })
    await user.click(within(rail).getByRole('link', { name: 'New conversation' }))

    expect(await screen.findByText(/Ask about a player/)).toBeInTheDocument()
    expect(screen.queryByText(/LeBron averaged/)).not.toBeInTheDocument()
  })

  it('чужой или несуществующий Диалог показывает Problem Details, а не пустой экран', async () => {
    api = installApiStub({ missing: ['no-such-conversation'] })
    renderApp('/chat/no-such-conversation')

    const error = await screen.findByRole('alert')
    expect(within(error).getByText('Conversation not found')).toBeInTheDocument()
  })
})
