import { useAuth } from '@clerk/clerk-react'
import { useMemo } from 'react'

import { createApiClient, type ApiClient } from '@/shared/api/client'

/** API-клиент, привязанный к сессии Clerk: `getToken` отдаёт свежий токен на каждый
 * запрос (Clerk сам обновляет короткоживущие сессионные JWT). Точка интеграции для
 * TanStack Query из среза #6. */
export function useApiClient(): ApiClient {
  const { getToken } = useAuth()

  return useMemo(() => {
    const baseUrl = import.meta.env.VITE_API_BASE_URL
    if (!baseUrl) {
      // Иначе запросы молча уходили бы на литеральный префикс "undefined/...".
      throw new Error('VITE_API_BASE_URL не задан: заполните его в .env (см. .env.example).')
    }
    return createApiClient({ baseUrl, getToken })
  }, [getToken])
}
