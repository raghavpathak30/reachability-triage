import { afterEach, describe, expect, test, vi } from 'vitest'
import { ApiError, NetworkError, createTriage, getHealth, getTriage } from './client'
import empty422 from './__fixtures__/422-empty-body.json'
import missingVersion422 from './__fixtures__/422-missing-version.json'
import validUuid404 from './__fixtures__/404-valid-uuid.json'
import unknownRoute404 from './__fixtures__/404-unknown-route.json'
import notUuid404 from './__fixtures__/404-not-a-uuid.json'
import reachableSample from '../../design/samples/stub-reachable.json'

interface Captured {
  status: number
  body: unknown
}

function stubResponse(captured: Captured) {
  const fn = vi.fn(async () => Response.json(captured.body, { status: captured.status }))
  vi.stubGlobal('fetch', fn)
  return fn
}

function firstCall(fn: ReturnType<typeof stubResponse>): [string, RequestInit] {
  const call = fn.mock.calls[0] as unknown as [string, RequestInit] | undefined
  if (call === undefined) {
    throw new Error('fetch was not called')
  }
  return call
}

async function catchError(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise
  } catch (error) {
    return error
  }
  throw new Error('expected promise to reject')
}

const body = { package: 'six', version: '1.16.0', target_module: 'six' }

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('error envelopes captured from the real app', () => {
  test('422 gives VALIDATION_ERROR with details', async () => {
    stubResponse(empty422)
    const error = await catchError(createTriage(body))
    expect(error).toBeInstanceOf(ApiError)
    const apiError = error as ApiError
    expect(apiError.status).toBe(422)
    expect(apiError.code).toBe('VALIDATION_ERROR')
    expect(apiError.message).toBe('Field required')
    expect(apiError.details).toEqual([{ loc: ['body', 'target_module'], msg: 'Field required' }])
  })

  test('422 from the model validator keeps its message and body loc', async () => {
    stubResponse(missingVersion422)
    const apiError = (await catchError(createTriage(body))) as ApiError
    expect(apiError.code).toBe('VALIDATION_ERROR')
    expect(apiError.message).toBe("Both 'package' and 'version' must be provided together.")
    expect(apiError.details?.[0]?.loc).toEqual(['body'])
  })

  test('404 for a valid uuid gives NOT_FOUND', async () => {
    stubResponse(validUuid404)
    const apiError = (await catchError(
      getTriage('00000000-0000-0000-0000-000000000000'),
    )) as ApiError
    expect(apiError.status).toBe(404)
    expect(apiError.code).toBe('NOT_FOUND')
    expect(apiError.details).toBeUndefined()
  })

  test('404 for an unknown route gives HTTP_ERROR', async () => {
    stubResponse(unknownRoute404)
    const apiError = (await catchError(getHealth())) as ApiError
    expect(apiError.status).toBe(404)
    expect(apiError.code).toBe('HTTP_ERROR')
  })

  test('404 for a non-uuid id gives NOT_FOUND', async () => {
    stubResponse(notUuid404)
    const apiError = (await catchError(getTriage('not-a-uuid'))) as ApiError
    expect(apiError.status).toBe(404)
    expect(apiError.code).toBe('NOT_FOUND')
  })
})

describe('success paths', () => {
  test('createTriage returns the parsed 202 body', async () => {
    const fn = stubResponse({ status: 202, body: reachableSample })
    const result = await createTriage(body)
    expect(result.id).toBe(reachableSample.id)
    expect(result.status).toBe('completed')
    expect(result.finding?.result.verdict).toBe(reachableSample.finding.result.verdict)
    expect(result.finding?.result.path?.length).toBe(reachableSample.finding.result.path.length)
    expect(result.llm_mode).toBe(reachableSample.llm_mode)
    expect(fn).toHaveBeenCalledTimes(1)
  })

  test('getTriage returns the parsed 200 body', async () => {
    stubResponse({ status: 200, body: reachableSample })
    const result = await getTriage(reachableSample.id)
    expect(result.finding?.tool_calls?.length).toBe(reachableSample.finding.tool_calls.length)
  })

  test('getTriage url-encodes the id', async () => {
    const fn = stubResponse({ status: 200, body: reachableSample })
    await getTriage('a/b?c')
    expect(firstCall(fn)[0]).toBe('/v1/triage/a%2Fb%3Fc')
  })

  test('createTriage sends a JSON body without repo_url', async () => {
    const fn = stubResponse({ status: 202, body: reachableSample })
    await createTriage({ ...body, target_symbol: 'parse' })
    const [url, init] = firstCall(fn)
    expect(url).toBe('/v1/triage')
    expect(init.method).toBe('POST')
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' })
    const sent = JSON.parse(init.body as string) as Record<string, unknown>
    expect(sent).toEqual({ ...body, target_symbol: 'parse' })
    expect('repo_url' in sent).toBe(false)
  })

  test('getHealth returns the status body', async () => {
    stubResponse({ status: 200, body: { status: 'ok' } })
    expect(await getHealth()).toEqual({ status: 'ok' })
  })
})

describe('failures outside the error envelope', () => {
  test('an HTML 502 gives UNPARSEABLE_ERROR', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('<html>Bad Gateway</html>', { status: 502 })),
    )
    const apiError = (await catchError(getTriage('x'))) as ApiError
    expect(apiError).toBeInstanceOf(ApiError)
    expect(apiError.status).toBe(502)
    expect(apiError.code).toBe('UNPARSEABLE_ERROR')
  })

  test('JSON of the wrong shape gives UNPARSEABLE_ERROR', async () => {
    stubResponse({ status: 500, body: { detail: 'boom' } })
    const apiError = (await catchError(getTriage('x'))) as ApiError
    expect(apiError.code).toBe('UNPARSEABLE_ERROR')
    expect(apiError.status).toBe(500)
  })

  test('a rejected fetch gives NetworkError, not ApiError', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )
    const error = await catchError(getTriage('x'))
    expect(error).toBeInstanceOf(NetworkError)
    expect(error).not.toBeInstanceOf(ApiError)
  })

  test('an aborted signal rejects with AbortError', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url: string, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            const signal = init?.signal
            if (signal === undefined || signal === null) {
              reject(new Error('signal was not passed to fetch'))
              return
            }
            const rejectAborted = () => reject(new DOMException('Aborted', 'AbortError'))
            if (signal.aborted) {
              rejectAborted()
              return
            }
            signal.addEventListener('abort', rejectAborted)
          }),
      ),
    )
    const controller = new AbortController()
    const pending = catchError(getTriage('x', controller.signal))
    controller.abort()
    const error = await pending
    expect(error).toBeInstanceOf(DOMException)
    expect((error as DOMException).name).toBe('AbortError')
  })

  test('a 2xx response with a non-JSON body gives ApiError, not a bare SyntaxError', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>ok</html>', { status: 200 })))
    const error = await catchError(getTriage('x'))
    expect(error).toBeInstanceOf(ApiError)
    expect(error).not.toBeInstanceOf(SyntaxError)
    expect((error as ApiError).code).toBe('UNPARSEABLE_RESPONSE')
    expect((error as ApiError).status).toBe(200)
  })

  test('an abort while the error body is being read rejects with AbortError', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: RequestInit) => {
        const signal = init?.signal
        if (signal === undefined || signal === null) {
          throw new Error('signal was not passed to fetch')
        }
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            signal.addEventListener('abort', () =>
              controller.error(new DOMException('Aborted', 'AbortError')),
            )
          },
        })
        return new Response(stream, { status: 500 })
      }),
    )
    const controller = new AbortController()
    const pending = catchError(getTriage('x', controller.signal))
    await new Promise((resolve) => setTimeout(resolve, 0))
    controller.abort()
    const error = await pending
    expect(error).toBeInstanceOf(DOMException)
    expect((error as DOMException).name).toBe('AbortError')
    expect(error).not.toBeInstanceOf(ApiError)
  })
})
