import { render, screen } from '@testing-library/react'

import { App } from '@/app/App'

describe('App', () => {
  it('рендерит app shell с названием продукта', () => {
    render(<App />)

    expect(screen.getByText('Court Vision')).toBeInTheDocument()
  })
})
