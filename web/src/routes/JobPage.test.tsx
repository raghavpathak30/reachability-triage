import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import notFound from '../api/__fixtures__/404-valid-uuid.json'
import failed from '../../design/samples/failed.derived.json'
import groqMode from '../../design/samples/groq-mode.derived.json'
import noEvidence from '../../design/samples/no-evidence.derived.json'
import nullMode from '../../design/samples/null-mode.derived.json'
import repeated from '../../design/samples/repeated-calls.derived.json'
import notReachable from '../../design/samples/stub-not_reachable.json'
import reachable from '../../design/samples/stub-reachable.json'
import unknown from '../../design/samples/stub-unknown.json'
import { RECENT_JOBS_KEY } from '../lib/recentJobs'
import JobPage from './JobPage'

type Outcome = { kind: 'body'; status: number; body: unknown } | { kind: 'network' } | { kind: 'hang' }

const body = (b: unknown): Outcome => ({ kind: 'body', status: 200, body: b })

function installFetch(script: Outcome[]) {
  const fetchMock = vi.fn((_input: unknown, init?: RequestInit) => {
    const index = fetchMock.mock.calls.length - 1
    const outcome = script[Math.min(index, script.length - 1)]
    const signal = init?.signal
    if (outcome === undefined) throw new Error('empty script')
    switch (outcome.kind) {
      case 'body':
        return Promise.resolve(Response.json(outcome.body, { status: outcome.status }))
      case 'network':
        return Promise.reject(new TypeError('offline'))
      case 'hang':
        return new Promise<Response>((_resolve, reject) => {
          signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
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

async function renderJob(script: Outcome[], id: string) {
  installFetch(script)
  const view = render(
    <MemoryRouter initialEntries={[`/jobs/${id}`]}>
      <Routes>
        <Route path="/" element={<p>home</p>} />
        <Route path="/jobs/:id" element={<JobPage />} />
      </Routes>
    </MemoryRouter>,
  )
  await advance(0)
  return view
}

beforeEach(() => {
  vi.useFakeTimers()
  window.localStorage.clear()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

const REACHABLE_ID = reachable.id

test('R1 renders the verdict together with its path (reachable sample)', async () => {
  const { container } = await renderJob([body(reachable)], REACHABLE_ID)
  const verdict = container.querySelector('section.verdict')
  expect(verdict).not.toBeNull()
  expect(within(verdict as HTMLElement).getByText('Reachable')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: /Call path/ })).toBeInTheDocument()
  expect(container.querySelectorAll('tbody tr')).toHaveLength(3)
})

test('R1 renders the verdict together with its reason (not_reachable sample)', async () => {
  const { container } = await renderJob([body(notReachable)], notReachable.id)
  const reason = container.querySelector('.reason')
  expect(reason).not.toBeNull()
  expect(reason).toHaveTextContent('no call path found from any entrypoint to pkg.sink:vulnerable')
  expect(container.querySelector('table')).toBeNull()
})

test('R1 shows a no evidence returned warning when path and reason are both null', async () => {
  await renderJob([body(noEvidence)], noEvidence.id)
  expect(screen.getByRole('alert')).toHaveTextContent('No evidence returned')
  expect(screen.getByText('Unknown')).toBeInTheDocument()
})

test('R1 an unrecognised verdict renders neutral Unknown with the raw value as text', async () => {
  const odd = structuredClone(reachable)
  ;(odd.finding.result as { verdict: string }).verdict = 'maybe'
  await renderJob([body(odd)], odd.id)
  expect(screen.getByText('Unknown')).toBeInTheDocument()
  expect(screen.getByText(/Unrecognised verdict value: maybe/)).toBeInTheDocument()
})

test('R1 completed with no finding shows an explicit no result warning', async () => {
  await renderJob([body({ id: REACHABLE_ID, status: 'completed', finding: null })], REACHABLE_ID)
  expect(screen.getByRole('alert')).toHaveTextContent('No result returned')
})

test('R2 unknown renders neutral with the reason prominent', async () => {
  const { container } = await renderJob([body(unknown)], unknown.id)
  const value = screen.getByText('Unknown')
  expect(value).toHaveClass('value')
  expect(value).not.toHaveClass('reachable')
  expect(container.querySelector('.reason')).toHaveTextContent('Reason.')
  expect(container.querySelector('.reason')).toHaveTextContent('carries no literal target name')
})

test('R2 not_reachable is labelled no path found and has no safe or success styling', async () => {
  const { container } = await renderJob([body(notReachable)], notReachable.id)
  const value = screen.getByText('No path found')
  expect(value.className).toBe('value')
  expect(container.textContent).not.toMatch(/\bsafe\b/i)
})

test('R2 reachable_only_from_tests uses its own label and the caution class', async () => {
  const tests = structuredClone(reachable)
  ;(tests.finding.result as { verdict: string }).verdict = 'reachable_only_from_tests'
  await renderJob([body(tests)], tests.id)
  const value = screen.getByText('Reachable only from tests')
  expect(value).toHaveClass('tests-only')
})

test('R3 renders ordered edges with location, confidence and rule', async () => {
  const { container } = await renderJob([body(reachable)], REACHABLE_ID)
  const rows = Array.from(container.querySelectorAll('tbody tr'))
  const cells = rows.map((r) => Array.from(r.querySelectorAll('td')).map((td) => td.textContent))
  expect(cells[0]).toEqual(['1', 'pkg.entry', 'pkg.layer_a:step_a', 'pkg/entry.py:4', 'high', 'import_alias'])
  expect(cells[2]).toEqual(['3', 'pkg.layer_b:step_b', 'pkg.sink:vulnerable', 'pkg/layer_b.py:5', 'high', 'import_alias'])
  expect(screen.getByRole('heading', { name: /3 edges, 0 low confidence/ })).toBeInTheDocument()
})

test('R3 flags low confidence edges and states the count in the path header', async () => {
  const { container } = await renderJob([body(unknown)], unknown.id)
  expect(screen.getByRole('heading', { name: /2 edges, 1 low confidence/ })).toBeInTheDocument()
  const rows = container.querySelectorAll('tbody tr')
  expect(rows[0]).not.toHaveClass('low')
  expect(rows[1]).toHaveClass('low')
  expect(rows[1]?.querySelector('td.conf-low')).toHaveTextContent('low')
})

test('R3 titles the table Partial path when verdict is unknown and path is non-empty', async () => {
  await renderJob([body(unknown)], unknown.id)
  expect(screen.getByRole('heading', { name: /Partial path/ })).toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: /Call path/ })).toBeNull()
})

test('R4 stub mode label', async () => {
  await renderJob([body(reachable)], REACHABLE_ID)
  expect(screen.getByText('stub (deterministic policy, not a real model)')).toBeInTheDocument()
})

test('R4 groq label', async () => {
  await renderJob([body(groqMode)], groqMode.id)
  expect(screen.getByText('groq')).toBeInTheDocument()
})

test('R4 null renders not recorded', async () => {
  await renderJob([body(nullMode)], nullMode.id)
  const dt = screen.getByText('LLM mode')
  expect(dt.nextElementSibling).toHaveTextContent('not recorded')
})

test('R5 labels the rationale as agent commentary with the not-used-for-verdict note', async () => {
  const { container } = await renderJob([body(reachable)], REACHABLE_ID)
  expect(screen.getByRole('heading', { name: 'Agent commentary' })).toBeInTheDocument()
  expect(
    screen.getByText('Written by the agent to explain its steps. Not used to derive the verdict.'),
  ).toBeInTheDocument()
  const quote = container.querySelector('blockquote.commentary')
  expect(quote).toHaveTextContent("confirmed_node_id='pkg.sink:vulnerable'")
  expect(container.querySelector('section.verdict')).not.toHaveTextContent('confirmed_node_id')
})

test('R6 trace is collapsible and renders five separate rows for stub-reachable', async () => {
  const { container } = await renderJob([body(reachable)], REACHABLE_ID)
  const details = container.querySelector('details')
  expect(details).not.toBeNull()
  expect(details).not.toHaveAttribute('open')
  expect(within(details as HTMLElement).getByText('Tool calls (5)')).toBeInTheDocument()
  expect(container.querySelectorAll('.call')).toHaveLength(5)
  expect(container.querySelectorAll('.repeat')).toHaveLength(0)
  expect(container.textContent).not.toContain('same call')
})

test('R6 orders by sequence even when the response is out of order', async () => {
  const shuffled = structuredClone(repeated)
  shuffled.finding.tool_calls.reverse()
  const { container } = await renderJob([body(shuffled)], shuffled.id)
  const heads = Array.from(container.querySelectorAll('.call-head > span.muted')).map((s) => s.textContent)
  expect(heads).toEqual(['0', '1', '2–4', '5'])
})

test('R6 groups consecutive identical calls with a count', async () => {
  const { container } = await renderJob([body(repeated)], repeated.id)
  expect(container.querySelectorAll('.call')).toHaveLength(4)
  expect(container.querySelector('.repeat')).toHaveTextContent('same call 3 times in a row')
  expect(screen.getByText('Tool calls (6)')).toBeInTheDocument()
})

test('R6 missing tool_calls renders no trace without throwing', async () => {
  const noTrace = structuredClone(reachable) as { finding: { tool_calls?: unknown } }
  delete noTrace.finding.tool_calls
  const { container } = await renderJob([body(noTrace)], REACHABLE_ID)
  expect(container.querySelector('details')).toBeNull()
  expect(screen.getByText('Reachable')).toBeInTheDocument()
})

test('R7 failed job shows the error string', async () => {
  const { container } = await renderJob([body(failed)], failed.id)
  const summary = container.querySelector('.error-summary')
  expect(summary).toHaveTextContent('Job failed')
  expect(summary).toHaveTextContent('AcquisitionError: pip download failed for')
  expect(summary).toHaveTextContent('No matching distribution found')
  expect(container.querySelector('section.verdict')).toBeNull()
})

test('R7 404 shows job not found with the id', async () => {
  await renderJob([{ kind: 'body', status: notFound.status, body: notFound.body }], 'abc')
  expect(screen.getByRole('heading', { name: 'Job not found' })).toBeInTheDocument()
  expect(screen.getByText('abc')).toBeInTheDocument()
})

test('R7 network failure keeps the last good status and id and shows retry', async () => {
  const running = { id: REACHABLE_ID, status: 'running', finding: null, error: null, llm_mode: null }
  const fetchMock = installFetch([body(running), { kind: 'network' }])
  render(
    <MemoryRouter initialEntries={[`/jobs/${REACHABLE_ID}`]}>
      <Routes>
        <Route path="/jobs/:id" element={<JobPage />} />
      </Routes>
    </MemoryRouter>,
  )
  await advance(0)
  expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull()
  await advance(1000)
  expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  const before = fetchMock.mock.calls.length
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  await advance(0)
  expect(fetchMock.mock.calls.length).toBe(before + 1)
  expect(screen.getByText('Running', { selector: 'dd' })).toBeInTheDocument()
  expect(screen.getByText(REACHABLE_ID)).toBeInTheDocument()
})

test('R7 running state reads Running. Last checked N seconds ago.', async () => {
  const running = { id: REACHABLE_ID, status: 'running', finding: null, error: null, llm_mode: null }
  await renderJob([body(running), { kind: 'hang' }], REACHABLE_ID)
  expect(screen.getByText('Running. Last checked 0 seconds ago.')).toBeInTheDocument()
  await advance(3000)
  expect(screen.getByText('Running. Last checked 3 seconds ago.')).toBeInTheDocument()
})

test('R7 queued state reads Queued.', async () => {
  const queued = { id: REACHABLE_ID, status: 'queued', finding: null, error: null, llm_mode: null }
  await renderJob([body(queued)], REACHABLE_ID)
  expect(screen.getByText('Queued.')).toBeInTheDocument()
  expect(screen.queryByText(/Running\. Last checked/)).toBeNull()
})

test('R7 capped state reads Stopped checking after 10 minutes. with Resume checking and restarts polling', async () => {
  const running = { id: REACHABLE_ID, status: 'running', finding: null, error: null, llm_mode: null }
  const fetchMock = installFetch([body(running)])
  render(
    <MemoryRouter initialEntries={[`/jobs/${REACHABLE_ID}`]}>
      <Routes>
        <Route path="/jobs/:id" element={<JobPage />} />
      </Routes>
    </MemoryRouter>,
  )
  await advance(600000)
  const line = screen.getByText(/Stopped checking after 10 minutes\./)
  expect(line).toBeInTheDocument()
  const frozen = fetchMock.mock.calls.length
  fireEvent.click(screen.getByRole('button', { name: 'Resume checking' }))
  await advance(0)
  expect(fetchMock.mock.calls.length).toBe(frozen + 1)
  expect(screen.queryByText(/Stopped checking/)).toBeNull()
})

test('R9 shows file paths after /extracted/', async () => {
  const { container } = await renderJob([body(reachable)], REACHABLE_ID)
  expect(container.textContent).toContain('pkg/entry.py:4')
  expect(container.querySelector('table')?.textContent).not.toContain('/tmp/')
})

test('R9 replaces a mid-sentence path in reason', async () => {
  const { container } = await renderJob([body(unknown)], unknown.id)
  const reason = container.querySelector('.reason')
  expect(reason?.textContent).toContain('at pkg/entry.py:5 carries')
  expect(reason?.textContent).not.toContain('/tmp/')
})

test('R9 renders the redaction marker muted with the sandbox title', async () => {
  const { container } = await renderJob([body(reachable)], REACHABLE_ID)
  const marker = container.querySelector('.call pre .redacted')
  expect(marker).toHaveTextContent('[REDACTED: instruction-like text]')
  expect(marker).toHaveAttribute(
    'title',
    'Removed by the injection sandbox. Absolute paths are always redacted.',
  )
})

test('R9 renders the redaction marker inside a failed error string', async () => {
  const { container } = await renderJob([body(failed)], failed.id)
  expect(container.querySelector('.error-summary .redacted')).toHaveAttribute('title')
})

test('target falls back to RecentJob then to the id while there is no finding', async () => {
  const queued = { id: REACHABLE_ID, status: 'queued', finding: null, error: null, llm_mode: null }
  await renderJob([body(queued)], REACHABLE_ID)
  expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(`Job ${REACHABLE_ID.slice(0, 8)}`)
  cleanup()

  window.localStorage.setItem(
    RECENT_JOBS_KEY,
    JSON.stringify([{ id: REACHABLE_ID, target: 'pkg.sink:vulnerable', pkg: 'x==1', submittedAt: '2026-01-01T00:00:00Z' }]),
  )
  await renderJob([body(queued)], REACHABLE_ID)
  expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('pkg.sink:vulnerable')
  expect(screen.getByText('Target').nextElementSibling).toHaveTextContent('not recorded')
})

test('tampered localStorage never changes the verdict, target fact or path table and the h1 shows it as text only', async () => {
  const evil = '<img src=x onerror=alert(1)>'
  window.localStorage.setItem(
    RECENT_JOBS_KEY,
    JSON.stringify([{ id: REACHABLE_ID, target: evil, pkg: 'x==1', submittedAt: '2026-01-01T00:00:00Z' }]),
  )
  const queued = { id: REACHABLE_ID, status: 'queued', finding: null, error: null, llm_mode: null }
  const first = await renderJob([body(queued)], REACHABLE_ID)
  expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(evil)
  expect(first.container.querySelector('img')).toBeNull()
  cleanup()

  const done = await renderJob([body(reachable)], REACHABLE_ID)
  expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('pkg.sink:vulnerable')
  expect(screen.getByText('Target').nextElementSibling).toHaveTextContent('pkg.sink:vulnerable')
  expect(done.container.textContent).not.toContain(evil)
  expect(done.container.querySelectorAll('tbody tr')).toHaveLength(3)
  expect(screen.getByText('Reachable')).toBeInTheDocument()
})

test('R6 arguments render as raw pretty JSON with no path trimming', async () => {
  const withArgs: { id: string; finding: { tool_calls: unknown } } = structuredClone(repeated)
  const raw = { a: '/x', file: '/tmp/q/extracted/a.py', other: '/home/u/keep.py' }
  withArgs.finding.tool_calls = [{ sequence: 0, tool_name: 'read_file', arguments: raw, result: '[]' }]
  const { container } = await renderJob([body(withArgs)], withArgs.id)
  expect(container.querySelector('pre.args')?.textContent).toBe(JSON.stringify(raw, null, 2))
})

test('R9 shows a tool result containing /extracted/ exactly as received', async () => {
  const withResult: { id: string; finding: { tool_calls: unknown } } = structuredClone(repeated)
  const raw = "opened /tmp/q/extracted/pkg/a.py and PosixPath('[REDACTED: instruction-like text]')"
  withResult.finding.tool_calls = [{ sequence: 0, tool_name: 'read_file', arguments: {}, result: raw }]
  const { container } = await renderJob([body(withResult)], withResult.id)
  const pre = container.querySelector('.call pre:not(.args)')
  expect(pre?.textContent).toBe(raw)
  expect(pre?.querySelector('.redacted')).toHaveAttribute('title')
})

test('R9 shows a failed error string containing /extracted/ exactly as received', async () => {
  const raw = 'AcquisitionError: bad wheel at /tmp/q/extracted/pkg/a.py [REDACTED: instruction-like text]'
  const { container } = await renderJob([body({ ...failed, error: raw })], failed.id)
  const summary = container.querySelector('.error-summary p')
  expect(summary?.textContent).toBe(raw)
  expect(summary?.querySelector('.redacted')).toHaveAttribute('title')
})

test('R9 shows commentary exactly as received', async () => {
  const withNote = structuredClone(reachable)
  withNote.finding.rationale = 'looked at /tmp/q/extracted/pkg/a.py'
  const { container } = await renderJob([body(withNote)], withNote.id)
  expect(container.querySelector('blockquote.commentary')?.textContent).toBe('looked at /tmp/q/extracted/pkg/a.py')
})

test('R9 renders a redaction marker inside tool arguments muted with the sandbox title', async () => {
  const withArgs: { id: string; finding: { tool_calls: unknown } } = structuredClone(repeated)
  const raw = { path: '[REDACTED: instruction-like text]', keep: '/tmp/q/extracted/a.py' }
  withArgs.finding.tool_calls = [{ sequence: 0, tool_name: 'read_file', arguments: raw, result: '[]' }]
  const { container } = await renderJob([body(withArgs)], withArgs.id)
  const args = container.querySelector('pre.args')
  expect(args?.textContent).toBe(JSON.stringify(raw, null, 2))
  expect(args?.querySelector('.redacted')).toHaveAttribute(
    'title',
    'Removed by the injection sandbox. Absolute paths are always redacted.',
  )
})
