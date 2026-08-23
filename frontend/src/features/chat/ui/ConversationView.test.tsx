import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { createMockAnswerStream } from '@/features/chat/test/fixtureStream'
import { ConversationView } from '@/features/chat/ui/ConversationView'
import { renderWithProviders } from '@/test/render'

// Поток без пауз: механику проверяем на последовательности событий, а не на таймерах.
const instantStream = createMockAnswerStream({ sleep: () => Promise.resolve() })

function renderConversation() {
  const user = userEvent.setup()
  renderWithProviders(<ConversationView stream={instantStream} />)
  return { user, composer: screen.getByLabelText('Ask a question') }
}

describe('ConversationView', () => {
  it('на пустом Диалоге предлагает вопрос и отвечает на него', async () => {
    const { user } = renderConversation()

    const question = 'How many points did LeBron James average in 2012-13?'
    await user.click(screen.getByRole('button', { name: question }))

    expect(await screen.findByText(/LeBron averaged/)).toBeInTheDocument()
    // Реплика пользователя осталась в ленте, ответ Агента — рядом.
    expect(screen.getByText(question)).toBeInTheDocument()
  })

  it('показывает результат Инструмента в рейке Evidence со штампом источника', async () => {
    const { user, composer } = renderConversation()

    await user.type(composer, 'How has Jokić’s passing changed since 2023?{Enter}')

    const rail = screen.getByRole('complementary', { name: 'Evidence' })
    expect(await within(rail).findByText('get_player_stats')).toBeInTheDocument()
    expect(await within(rail).findByText('search_corpus')).toBeInTheDocument()
    expect(within(rail).getByText('211 ms')).toBeInTheDocument()
    expect(within(rail).getByText('3.61')).toBeInTheDocument()
  })

  it('во время стриминга держит курсор печати и убирает его на done', async () => {
    const { user, composer } = renderConversation()

    await user.type(composer, 'lebron{Enter}')

    expect(await screen.findByText(/LeBron averaged/)).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByTestId('typing-cursor')).not.toBeInTheDocument()
    })
  })

  it('клик по цитате подсвечивает карточку Evidence, из которой взято утверждение', async () => {
    const { user, composer } = renderConversation()

    await user.type(composer, 'jokic{Enter}')
    await user.click(await screen.findByRole('button', { name: 'Source 2' }))

    const rail = screen.getByRole('complementary', { name: 'Evidence' })
    const cards = within(rail).getAllByRole('article')
    expect(cards[1]).toHaveAttribute('data-active', 'true')
    expect(cards[0]).toHaveAttribute('data-active', 'false')
  })

  it('событие error доходит до ленты состоянием ошибки с повтором, а не пустым ответом', async () => {
    const { user, composer } = renderConversation()

    await user.type(composer, 'Who won the 1971 finals?{Enter}')

    const error = await screen.findByRole('alert')
    expect(within(error).getByText('No scripted answer for this question')).toBeInTheDocument()
    expect(within(error).getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('повтор заменяет неудавшийся ответ, а вопрос в ленте не дублируется', async () => {
    const { user, composer } = renderConversation()

    await user.type(composer, 'Who won the 1971 finals?{Enter}')
    await user.click(await screen.findByRole('button', { name: 'Retry' }))

    await waitFor(() => {
      expect(screen.getAllByRole('alert')).toHaveLength(1)
    })
    expect(screen.getAllByText('Who won the 1971 finals?')).toHaveLength(1)
  })

  it('повторяют тот ответ, на кнопку которого нажали, а не последний в ленте', async () => {
    const { user, composer } = renderConversation()

    await user.type(composer, 'Who won the 1971 finals?{Enter}')
    await screen.findByRole('alert')
    await user.type(composer, 'Name the 1998 MVP{Enter}')
    await waitFor(() => {
      expect(screen.getAllByRole('alert')).toHaveLength(2)
    })

    const [firstError] = screen.getAllByRole('alert')
    await user.click(within(firstError as HTMLElement).getByRole('button', { name: 'Retry' }))

    // Оба вопроса на месте по разу, ответ на второй не тронут.
    await waitFor(() => {
      expect(screen.getAllByRole('alert')).toHaveLength(2)
    })
    expect(screen.getAllByText('Who won the 1971 finals?')).toHaveLength(1)
    expect(screen.getAllByText('Name the 1998 MVP')).toHaveLength(1)
  })

  it('оборванный поток доходит до ленты ошибкой, а не вечным курсором', async () => {
    const user = userEvent.setup()
    // Настоящий SSE может не дослать событие `error`, а просто упасть.
    const brokenStream = async function* () {
      await Promise.resolve()
      yield { type: 'token', text: 'Volume is up' } as const
      throw new Error('The connection dropped.')
    }
    renderWithProviders(<ConversationView stream={brokenStream} />)

    await user.type(screen.getByLabelText('Ask a question'), 'jokic{Enter}')

    const error = await screen.findByRole('alert')
    expect(within(error).getByText('The answer stream stopped')).toBeInTheDocument()
    expect(screen.queryByTestId('typing-cursor')).not.toBeInTheDocument()
    // Композер разблокирован: иначе после обрыва Диалог был бы мёртв до перезагрузки.
    expect(screen.getByLabelText('Ask a question')).toBeEnabled()
  })

  it('поток, кончившийся без done, не оставляет вечный курсор печати', async () => {
    const user = userEvent.setup()
    // Так выглядит таймаут прокси: тело закрылось, терминального события не было.
    const truncatedStream = async function* () {
      await Promise.resolve()
      yield { type: 'token', text: 'Volume is up' } as const
    }
    renderWithProviders(<ConversationView stream={truncatedStream} />)

    await user.type(screen.getByLabelText('Ask a question'), 'jokic{Enter}')

    const error = await screen.findByRole('alert')
    expect(within(error).getByText('The answer stream stopped')).toBeInTheDocument()
    expect(within(error).getByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(screen.queryByTestId('typing-cursor')).not.toBeInTheDocument()
    // Прочитанное остаётся на месте: пользователь это уже видел.
    expect(screen.getByText(/Volume is up/)).toBeInTheDocument()
  })

  it('уход с Диалога посреди ответа отменяет запрос', async () => {
    const user = userEvent.setup()
    // Ответ, который никогда не заканчивается сам: единственный способ его закрыть — отмена.
    let signal: AbortSignal | undefined
    const endlessStream = async function* (_question: string, options?: { signal?: AbortSignal }) {
      signal = options?.signal
      yield { type: 'token', text: 'Считаю…' } as const
      await new Promise(() => {
        // Тело потока не закрывается: так выглядит генерация, которая ещё идёт.
      })
    }
    const view = renderWithProviders(<ConversationView stream={endlessStream} />)

    await user.type(screen.getByLabelText('Ask a question'), 'jokic{Enter}')
    await screen.findByText('Считаю…')
    expect(signal?.aborted).toBe(false)

    // Размонтирование — это переход на другой Диалог или выход из чата. Без отмены POST
    // остался бы открытым до конца генерации, и за неё платили бы впустую.
    view.unmount()

    expect(signal?.aborted).toBe(true)
  })

  it('пустой вопрос не отправляется', async () => {
    const { user, composer } = renderConversation()

    await user.type(composer, '   {Enter}')

    expect(screen.queryByRole('article')).not.toBeInTheDocument()
    expect(screen.getByText(/Ask about a player/)).toBeInTheDocument()
  })

  it('Shift+Enter переносит строку вместо отправки', async () => {
    const { user, composer } = renderConversation()

    await user.type(composer, 'Jokić{Shift>}{Enter}{/Shift}2023')

    expect(composer).toHaveValue('Jokić\n2023')
    expect(screen.getByText(/Ask about a player/)).toBeInTheDocument()
  })
})
