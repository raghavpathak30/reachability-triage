import { act, cleanup, render, renderHook } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import notFound from '../api/__fixtures__/404-valid-uuid.json'
import { nextDelay, usePollTriage } from './usePollTriage'

const ID = '057692ae-ca68-4ae7-b6a7-551dda6e3db5'

type Outcome =
  | { kind: 'body'; status: number; body: unknown }
  | { kind: 'network' }
  | { kind: 'unparseable' }
  | { kind: 'hang' }
  | { kind: 'late'; afterMs: number; body: unknown }

const running: Outcome = { kind: 'body', status: 200, body: { id: ID, status: 'running' } }
const completed: Outcome = { kind: 'body', status: 200, body: { id: ID, status: 'completed' } }
const failedJob: Outcome = { kind: 'body', status: 200, body: { id: ID, status: 'failed', error: 'boom' } }
const network: Outcome = { kind: 'network' }
const unparseable: Outcome = { kind: 'unparseable' }
const serverError: Outcome = {
  kind: 'body',
  status: 503,
  body: { error: { code: 'HTTP_ERROR', message: 'unavailable' } },
}

interface Call {
  url: string
  at: number
  signal: AbortSignal
}

let calls: Call[]
let start: number

function abortError() {
  return new DOMException('aborted', 'AbortError')
}

function installFetch(script: Outcome[]) {
  calls = []
  start = Date.now()
  const fetchMock = vi.fn((input: unknown, init?: RequestInit) => {
    const signal = init?.signal
    if (signal === undefined || signal === null) throw new Error('fetch called without a signal')
    const index = calls.length
    calls.push({ url: String(input), at: Date.now() - start, signal })
    const outcome = script[Math.min(index, script.length - 1)]
    if (outcome === undefined) throw new Error('empty script')
    if (signal.aborted) return Promise.reject(abortError())
    switch (outcome.kind) {
      case 'body':
        return Promise.resolve(Response.json(outcome.body, { status: outcome.status }))
      case 'network':
        return Promise.reject(new TypeError('offline'))
      case 'unparseable':
        return Promise.resolve(new Response('<html>', { status: 200 }))
      case 'late':
        // Ignores the abort signal and resolves after afterMs, like a fetch that
        // does not honour cancellation.
        return new Promise<Response>((resolve) => {
          setTimeout(() => resolve(Response.json(outcome.body, { status: 200 })), outcome.afterMs)
        })
      case 'hang':
        return new Promise<Response>((_resolve, reject) => {
          signal.addEventListener('abort', () => reject(abortError()))
        })
    }
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

test('nextDelay backs off x1.5 to a 5000 ms ceiling', () => {
  expect(nextDelay(1000)).toBe(1500)
  expect(nextDelay(1500)).toBe(2250)
  expect(nextDelay(2250)).toBe(3375)
  expect(nextDelay(3375)).toBe(5000)
  expect(nextDelay(5000)).toBe(5000)
})

test('requests follow the backoff schedule', async () => {
  installFetch([running])
  renderHook(() => usePollTriage(ID))
  await advance(13125)
  expect(calls.map((c) => c.at)).toEqual([0, 1000, 2500, 4750, 8125, 13125])
  expect(calls[0]?.url).toBe(`/v1/triage/${ID}`)
})

test.each([
  ['completed', completed],
  ['failed', failedJob],
])('stops after a %s body', async (_name, outcome) => {
  installFetch([running, outcome])
  const { result } = renderHook(() => usePollTriage(ID))
  await advance(60000)
  expect(calls).toHaveLength(2)
  expect(result.current.state).toBe('done')
  expect(result.current.data?.status).toBe(_name)
})

test('stops at the 10 minute cap with no request after the deadline, and resume restarts', async () => {
  installFetch([running])
  const { result } = renderHook(() => usePollTriage(ID))
  await advance(600000)
  expect(result.current.state).toBe('capped')
  expect(result.current.data?.status).toBe('running')
  expect(calls.every((c) => c.at < 600000)).toBe(true)
  const frozen = calls.length
  await advance(120000)
  expect(calls).toHaveLength(frozen)

  act(() => result.current.resume())
  await advance(0)
  expect(result.current.state).toBe('polling')
  expect(calls).toHaveLength(frozen + 1)
  await advance(1000)
  expect(calls).toHaveLength(frozen + 2)
  expect(calls[frozen + 1]!.at - calls[frozen]!.at).toBe(1000)
})

test('the cap is measured from the first poll across responses', async () => {
  installFetch([running])
  const { result } = renderHook(() => usePollTriage(ID))
  await advance(599000)
  expect(result.current.state).toBe('polling')
  await advance(1001)
  expect(result.current.state).toBe('capped')
})

test('an in-flight request at the deadline is aborted and its result discarded', async () => {
  installFetch([running, { kind: 'hang' }])
  const { result } = renderHook(() => usePollTriage(ID))
  await advance(1000)
  expect(calls).toHaveLength(2)
  expect(calls[1]?.signal.aborted).toBe(false)
  await advance(600000)
  expect(calls[1]?.signal.aborted).toBe(true)
  expect(result.current.state).toBe('capped')
  expect(result.current.error).toBeNull()
  expect(result.current.data?.status).toBe('running')
  expect(calls).toHaveLength(2)
})

test('unmount aborts the in-flight request and later requests never fire', async () => {
  installFetch([{ kind: 'hang' }])
  const { unmount } = renderHook(() => usePollTriage(ID))
  await advance(0)
  expect(calls[0]?.signal.aborted).toBe(false)
  unmount()
  expect(calls[0]?.signal.aborted).toBe(true)
  await advance(60000)
  expect(calls).toHaveLength(1)
})

test.each([
  ['network error', network],
  ['5xx', serverError],
  ['unparseable response', unparseable],
])('one %s blip then success keeps polling and resets the count', async (_name, blip) => {
  installFetch([running, blip, running])
  const { result } = renderHook(() => usePollTriage(ID))
  await advance(1000)
  expect(result.current.failureCount).toBe(1)
  expect(result.current.state).toBe('polling')
  await advance(1500)
  expect(calls).toHaveLength(3)
  expect(result.current.failureCount).toBe(0)
  expect(result.current.state).toBe('polling')
  await advance(2250)
  expect(calls).toHaveLength(4)
})

test.each([
  ['network error', network],
  ['5xx', serverError],
  ['unparseable response', unparseable],
])('three consecutive %s failures stop polling, keep data, and retry restarts', async (_name, failure) => {
  installFetch([running, failure, failure, failure, running])
  const { result } = renderHook(() => usePollTriage(ID))
  await advance(4750)
  expect(calls).toHaveLength(4)
  expect(result.current.state).toBe('error')
  expect(result.current.failureCount).toBe(3)
  expect(result.current.data?.status).toBe('running')
  await advance(60000)
  expect(calls).toHaveLength(4)

  act(() => result.current.retry())
  // Immediately after retry(), before the new request resolves: state is reset
  // and the restarted loop has issued its first request at once.
  expect(result.current.failureCount).toBe(0)
  expect(result.current.error).toBeNull()
  expect(result.current.state).toBe('polling')
  expect(result.current.data?.status).toBe('running')
  expect(calls).toHaveLength(5)
  const retryAt = calls[4]?.at ?? -1
  await advance(0)
  expect(result.current.state).toBe('polling')
  expect(result.current.failureCount).toBe(0)
  await advance(999)
  expect(calls).toHaveLength(5)
  await advance(1)
  expect(calls).toHaveLength(6)
  expect((calls[5]?.at ?? 0) - retryAt).toBe(1000)
})

test('a success between failures resets the consecutive count', async () => {
  installFetch([network, network, running, network, network, running])
  const { result } = renderHook(() => usePollTriage(ID))
  await advance(1000)
  expect(calls.map((c) => c.at)).toEqual([0, 1000])
  expect(result.current.failureCount).toBe(2)
  await advance(1500)
  expect(calls.map((c) => c.at)).toEqual([0, 1000, 2500])
  expect(result.current.failureCount).toBe(0)
  expect(result.current.state).toBe('polling')
  await advance(2250)
  expect(calls.map((c) => c.at)).toEqual([0, 1000, 2500, 4750])
  expect(result.current.failureCount).toBe(1)
  await advance(3375)
  expect(calls.map((c) => c.at)).toEqual([0, 1000, 2500, 4750, 8125])
  expect(result.current.failureCount).toBe(2)
  expect(result.current.state).toBe('polling')
  await advance(5000)
  expect(calls.map((c) => c.at)).toEqual([0, 1000, 2500, 4750, 8125, 13125])
  expect(result.current.failureCount).toBe(0)
  expect(result.current.state).toBe('polling')
})

test('a response that ignores the abort signal and lands after the deadline is discarded', async () => {
  installFetch([running, { kind: 'late', afterMs: 700000, body: { id: ID, status: 'completed' } }])
  const { result } = renderHook(() => usePollTriage(ID))
  await advance(1000)
  expect(calls).toHaveLength(2)
  await advance(599000)
  expect(result.current.state).toBe('capped')
  expect(calls[1]?.signal.aborted).toBe(true)
  await advance(200000)
  expect(result.current.state).toBe('capped')
  expect(result.current.data?.status).toBe('running')
  expect(calls).toHaveLength(2)
})

test('capped clears an error left by earlier transient failures', async () => {
  installFetch([running, network, { kind: 'hang' }])
  const { result } = renderHook(() => usePollTriage(ID))
  await advance(1000)
  expect(result.current.error).not.toBeNull()
  expect(result.current.failureCount).toBe(1)
  await advance(600000)
  expect(result.current.state).toBe('capped')
  expect(result.current.error).toBeNull()
  expect(result.current.data?.status).toBe('running')
})

test('retry restarts the cap clock', async () => {
  installFetch([running, network, network, network, running])
  const { result } = renderHook(() => usePollTriage(ID))
  await advance(4750)
  expect(result.current.state).toBe('error')
  act(() => result.current.retry())
  await advance(599000)
  expect(result.current.state).toBe('polling')
  await advance(1001)
  expect(result.current.state).toBe('capped')
})

test('404 NOT_FOUND stops at once', async () => {
  installFetch([{ kind: 'body', status: notFound.status, body: notFound.body }])
  const { result } = renderHook(() => usePollTriage(ID))
  await advance(60000)
  expect(calls).toHaveLength(1)
  expect(result.current.state).toBe('not_found')
})

test.each([
  ['400', { kind: 'body', status: 400, body: { error: { code: 'BAD', message: 'bad' } } } as Outcome],
  ['422', { kind: 'body', status: 422, body: { error: { code: 'VALIDATION_ERROR', message: 'bad' } } } as Outcome],
  ['404 HTTP_ERROR', { kind: 'body', status: 404, body: { error: { code: 'HTTP_ERROR', message: 'nope' } } } as Outcome],
])('other 4xx (%s) stops at once as an error', async (_name, outcome) => {
  installFetch([outcome])
  const { result } = renderHook(() => usePollTriage(ID))
  await advance(60000)
  expect(calls).toHaveLength(1)
  expect(result.current.state).toBe('error')
  expect(result.current.error).not.toBeNull()
})

test('StrictMode leaves exactly one live loop', async () => {
  installFetch([running])
  function Probe() {
    usePollTriage(ID)
    return null
  }
  render(
    <StrictMode>
      <Probe />
    </StrictMode>,
  )
  await advance(0)
  expect(calls).toHaveLength(2)
  expect(calls[0]?.signal.aborted).toBe(true)
  expect(calls[1]?.signal.aborted).toBe(false)
  await advance(1000)
  expect(calls).toHaveLength(3)
  await advance(1500)
  expect(calls).toHaveLength(4)
})

test('changing id aborts the old loop and starts fresh without stale data', async () => {
  installFetch([running])
  const { result, rerender } = renderHook(({ id }) => usePollTriage(id), { initialProps: { id: ID } })
  await advance(0)
  expect(result.current.data?.status).toBe('running')
  const other = '11111111-1111-1111-1111-111111111111'
  rerender({ id: other })
  expect(calls[0]?.signal.aborted).toBe(true)
  expect(result.current.data).toBeNull()
  await advance(0)
  expect(calls[1]?.url).toBe(`/v1/triage/${other}`)
  await advance(1000)
  expect(calls.filter((c) => c.url.endsWith(ID))).toHaveLength(1)
})
