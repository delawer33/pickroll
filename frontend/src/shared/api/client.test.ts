import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, createApiClient } from '@/shared/api/client'

const okResponse = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })

describe('createApiClient', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('прикладывает Bearer-токен к запросу', async () => {
    const fetchMock = vi.fn().mockResolvedValue(okResponse({ clerk_user_id: 'user_1' }))
    vi.stubGlobal('fetch', fetchMock)

    const client = createApiClient({
      baseUrl: 'http://api.test',
      getToken: async () => 'jwt-123',
    })
    await client.get('/api/v1/me')

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('http://api.test/api/v1/me')
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer jwt-123')
  })

  it('без токена запрос уходит анонимным — Authorization не подделывается', async () => {
    const fetchMock = vi.fn().mockResolvedValue(okResponse({}))
    vi.stubGlobal('fetch', fetchMock)

    const client = createApiClient({ baseUrl: 'http://api.test', getToken: async () => null })
    await client.get('/api/v1/me')

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(new Headers(init.headers).get('Authorization')).toBeNull()
  })

  it('возвращает разобранный JSON-ответ', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(okResponse({ clerk_user_id: 'user_42' })),
    )

    const client = createApiClient({ baseUrl: 'http://api.test', getToken: async () => 't' })
    const body = await client.get<{ clerk_user_id: string }>('/api/v1/me')

    expect(body).toEqual({ clerk_user_id: 'user_42' })
  })

  it('не-2xx с Problem Details бросает ApiError с разобранной проблемой', async () => {
    const problem = {
      type: 'urn:court-vision:problem:unauthenticated',
      title: 'Unauthenticated',
      status: 401,
      detail: 'Запрос требует Bearer-токен.',
      instance: '/api/v1/me',
    }
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(problem), {
          status: 401,
          headers: { 'Content-Type': 'application/problem+json' },
        }),
      ),
    )

    const client = createApiClient({ baseUrl: 'http://api.test', getToken: async () => null })
    const failed = client.get('/api/v1/me')

    await expect(failed).rejects.toBeInstanceOf(ApiError)
    await expect(failed).rejects.toMatchObject({ problem })
  })

  it('не-JSON ошибка (HTML от прокси) превращается в ApiError со статусом', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response('<html>502 Bad Gateway</html>', {
          status: 502,
          headers: { 'Content-Type': 'text/html' },
        }),
      ),
    )

    const client = createApiClient({ baseUrl: 'http://api.test', getToken: async () => null })
    const failed = client.get('/api/v1/me')

    await expect(failed).rejects.toBeInstanceOf(ApiError)
    await expect(failed).rejects.toMatchObject({ problem: { status: 502 } })
  })

  it('отказ сети превращается в ApiError, а не сырой TypeError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    const client = createApiClient({ baseUrl: 'http://api.test', getToken: async () => null })
    const failed = client.get('/api/v1/me')

    await expect(failed).rejects.toBeInstanceOf(ApiError)
    await expect(failed).rejects.toMatchObject({
      problem: { type: 'urn:court-vision:problem:network' },
    })
  })
})
