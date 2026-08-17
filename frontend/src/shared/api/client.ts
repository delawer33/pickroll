/** Клиент к API Court Vision.
 *
 * Единственная точка, где к запросам прикладывается Bearer-токен Clerk и разбираются
 * ошибки — фичи не работают с fetch напрямую. Любой исход, кроме успешного JSON-ответа,
 * выходит наружу как `ApiError` с телом RFC 9457 Problem Details: настоящим от бэкенда
 * или синтезированным (HTML от прокси, отказ сети), чтобы вызывающему не разбирать зоопарк
 * из SyntaxError, TypeError и статусов.
 */

/** Тело ошибки RFC 9457; расширяющие члены (candidates, nearest, ...) — по ключу. */
export interface Problem {
  type: string
  title: string
  status: number
  detail: string
  instance: string
  [extension: string]: unknown
}

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
}

export function createApiClient({ baseUrl, getToken }: ApiClientOptions): ApiClient {
  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const token = await getToken()
    const headers = new Headers(init?.headers)
    if (token !== null) {
      headers.set('Authorization', `Bearer ${token}`)
    }

    let response: Response
    try {
      response = await fetch(`${baseUrl}${path}`, { ...init, headers })
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

  return {
    get: (path) => request(path),
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
