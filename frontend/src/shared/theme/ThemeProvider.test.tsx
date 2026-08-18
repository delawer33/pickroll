import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import { ThemeToggle } from '@/app/shell/ThemeToggle'
import { renderWithProviders } from '@/test/render'

describe('тема', () => {
  beforeEach(() => {
    window.localStorage.clear()
    delete document.documentElement.dataset.theme
  })

  it('по умолчанию берёт системную настройку', () => {
    renderWithProviders(<ThemeToggle />)

    // Заглушка matchMedia в setup.ts отвечает «не тёмная».
    expect(document.documentElement.dataset.theme).toBe('light')
  })

  it('переключение применяется к документу и переживает перезагрузку', async () => {
    const user = userEvent.setup()
    const { unmount } = renderWithProviders(<ThemeToggle />)

    await user.click(screen.getByRole('button', { name: 'Switch to dark theme' }))

    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(window.localStorage.getItem('cv-theme')).toBe('dark')

    unmount()
    renderWithProviders(<ThemeToggle />)

    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(screen.getByRole('button', { name: 'Switch to light theme' })).toBeInTheDocument()
  })
})
