/** Клиент к API Court Vision.
 *
 * Единственная точка, где к запросам прикладывается Bearer-токен Clerk и разбираются
 * ошибки — фичи не работают с fetch напрямую. Любой исход, кроме успешного JSON-ответа,
 * выходит наружу как `ApiError` с телом RFC 9457 Problem Details: настоящим от бэкенда
 * или синтезированным (HTML от прокси, отказ сети), чтобы вызывающему не разбирать зоопарк
 * из SyntaxError, TypeError и статусов.
 */

import { sseJson } from '@/shared/api/sse'
import type { Problem } from '@/shared/api/wire'

export type { Problem }

export class ApiError extends Error {
  readonly problem: Problem

  constructor(problem: Problem, options?: ErrorOptions) {
    super(problem.detail, options)
    this.name = 'ApiError'
    this.problem = problem
  }
}

/** Возвращает актуальный токен Clerk или null для анонимного запроса. */
export type TokenGetter = () => Promise<string | null>

export interface ApiClientOptions {
  baseUrl: string
  getToken: TokenGetter
}

export interface ApiClient {
  get<T>(path: string): Promise<T>
  post<T>(path: string, body: unknown): Promise<T>
  /** SSE-поток: события по мере поступления. Неуспешный ответ становится `ApiError` до
   * первого события — отказ на входе (чужой Диалог, негодное тело) обязан быть отличим от
   * ошибки внутри уже начавшегося потока.
   *
   * `signal` закрывает запрос: брошенный ответ иначе висит открытым до конца генерации на
   * сервере, а генерацию за него всё равно кто-то платит. */
  stream<EventT>(path: string, body: unknown, signal?: AbortSignal): AsyncIterable<EventT>
}

export function createApiClient({ baseUrl, getToken }: ApiClientOptions): ApiClient {
  async function send(path: string, init: RequestInit, accept: string): Promise<Response> {
    const token = await getToken()
    const headers = new Headers(init.headers)
    headers.set('Accept', accept)
    if (token !== null) {
      headers.set('Authorization', `Bearer ${token}`)
    }

    try {
      return await fetch(`${baseUrl}${path}`, { ...init, headers })
    } catch (cause) {
      throw new ApiError(
        {
          type: 'urn:court-vision:problem:network',
          title: 'Network error',
          status: 0,
          detail: 'Сеть недоступна или API не отвечает.',
          instance: path,
        },
        { cause },
      )
    }
  }

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await send(path, init, 'application/json')

    const body: unknown = await response
      .json()
      .catch(() => undefined) /* HTML от прокси или пустое тело — не JSON. */

    if (!response.ok) {
      throw new ApiError(isProblem(body) ? body : syntheticProblem(response, path))
    }
    if (body === undefined) {
      throw new ApiError(syntheticProblem(response, path))
    }
    return body as T
  }

  function json(body: unknown): RequestInit {
    return {
      method: 'POST',
      body: JSON.stringify(body),
      headers: { 'Content-Type': 'application/json' },
    }
  }

  async function* stream<EventT>(
    path: string,
    body: unknown,
    signal?: AbortSignal,
  ): AsyncGenerator<EventT> {
    const response = await send(
      path,
      { ...json(body), ...(signal !== undefined && { signal }) },
      'text/event-stream',
    )

    if (!response.ok) {
      const problem: unknown = await response.json().catch(() => undefined)
      throw new ApiError(isProblem(problem) ? problem : syntheticProblem(response, path))
    }
    if (response.body === null) {
      // Успешный ответ без тела — поток не начался, и ждать событий бессмысленно.
      throw new ApiError(syntheticProblem(response, path))
    }

    yield* sseJson<EventT>(response.body)
  }

  return {
    get: (path) => request(path),
    post: (path, body) => request(path, json(body)),
    stream,
  }
}

function isProblem(body: unknown): body is Problem {
  return (
    typeof body === 'object' &&
    body !== null &&
    'type' in body &&
    'title' in body &&
    'status' in body &&
    'detail' in body
  )
}

function syntheticProblem(response: Response, path: string): Problem {
  return {
    type: 'about:blank',
    title: response.statusText || 'HTTP error',
    status: response.status,
    detail: `Запрос ${path} завершился статусом ${response.status} без Problem Details.`,
    instance: path,
  }
}
