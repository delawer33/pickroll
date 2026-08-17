import { ClerkProvider } from '@clerk/clerk-react'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from '@/app/App'
import '@/shared/styles/index.css'
import { clerkAppearance } from '@/shared/styles/clerkAppearance'

const rootElement = document.getElementById('root')
if (!rootElement) {
  throw new Error('Root element #root not found')
}

// Ключ публичный (pk_*), но обязательный: без него Clerk не инициализируется. Сообщение
// пишем в саму страницу — брошенное из module scope исключение видно только в консоли,
// а пользователь смотрит на белый экран.
const publishableKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY
if (!publishableKey) {
  const message =
    'VITE_CLERK_PUBLISHABLE_KEY не задан: скопируйте .env.example в .env и заполните ключ Clerk.'
  rootElement.textContent = message
  throw new Error(message)
}

createRoot(rootElement).render(
  <StrictMode>
    <ClerkProvider publishableKey={publishableKey} appearance={clerkAppearance()}>
      <App />
    </ClerkProvider>
  </StrictMode>,
)
