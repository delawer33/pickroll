import { render, screen } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from '@/app/App'

// Clerk в юнит-тестах мокается целиком: его компоненты тянут clerk-js по сети.
// Мок повторяет контракт SignedIn/SignedOut — рендерить детей по состоянию сессии.
const authState = vi.hoisted(() => ({ signedIn: false }))

vi.mock('@clerk/clerk-react', () => ({
  ClerkLoading: () => null,
  SignedIn: ({ children }: PropsWithChildren) => (authState.signedIn ? children : null),
  SignedOut: ({ children }: PropsWithChildren) => (authState.signedIn ? null : children),
  SignIn: () => <div data-testid="clerk-sign-in" />,
  UserButton: () => <div data-testid="clerk-user-button" />,
}))

describe('App', () => {
  beforeEach(() => {
    authState.signedIn = false
  })

  it('без сессии показывает форму входа Clerk и прячет контент', () => {
    render(<App />)

    expect(screen.getByText('Court Vision')).toBeInTheDocument()
    expect(screen.getByTestId('clerk-sign-in')).toBeInTheDocument()
    expect(screen.queryByText('AI-ассистент баскетбольного скаута')).not.toBeInTheDocument()
  })

  it('с сессией показывает контент и кнопку пользователя', () => {
    authState.signedIn = true

    render(<App />)

    expect(screen.getByText('AI-ассистент баскетбольного скаута')).toBeInTheDocument()
    expect(screen.getByTestId('clerk-user-button')).toBeInTheDocument()
    expect(screen.queryByTestId('clerk-sign-in')).not.toBeInTheDocument()
  })
})
