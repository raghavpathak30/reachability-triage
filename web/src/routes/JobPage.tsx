import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router'
import type { components } from '../api/schema'
import Commentary from '../components/job/Commentary'
import PathTable from '../components/job/PathTable'
import RedactedText from '../components/job/RedactedText'
import ToolTrace from '../components/job/ToolTrace'
import VerdictSection from '../components/job/VerdictSection'
import { usePollTriage } from '../hooks/usePollTriage'
import { displayPath, llmModeLabel } from '../lib/displayText'
import { loadRecent } from '../lib/recentJobs'

type TriageOut = components['schemas']['TriageOut']

const STATUS_WORDS: Record<string, string> = {
  queued: 'Queued',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
}

function statusWord(status: string): string {
  return STATUS_WORDS[status] ?? status
}

function useSecondsSince(epochMs: number | null, active: boolean): number | null {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return undefined
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [active])
  if (epochMs === null) return null
  return Math.max(0, Math.floor((now - epochMs) / 1000))
}

function targetOf(data: TriageOut | null): string | null {
  const result = data?.finding?.result
  if (result === undefined) return null
  return result.target_symbol ? `${result.target_module}:${result.target_symbol}` : result.target_module
}

function StatusLine({
  data,
  state,
  errorMessage,
  seconds,
  onResume,
  onRetry,
}: {
  data: TriageOut | null
  state: string
  errorMessage: string | null
  seconds: number | null
  onResume: () => void
  onRetry: () => void
}) {
  if (errorMessage !== null) {
    return (
      <p className="status-line" role="alert">
        Could not check the job status: {errorMessage}{' '}
        <button type="button" className="link" onClick={onRetry}>
          Retry
        </button>
      </p>
    )
  }
  if (state === 'capped') {
    return (
      <p className="status-line muted small">
        Stopped checking after 10 minutes.{' '}
        <button type="button" className="link" onClick={onResume}>
          Resume checking
        </button>
      </p>
    )
  }
  if (state !== 'polling') return null
  if (data?.status === 'queued') return <p className="status-line">Queued.</p>
  if (seconds === null) return <p className="status-line">Running. Not checked yet.</p>
  return (
    <p className="status-line">
      Running. Last checked {seconds} {seconds === 1 ? 'second' : 'seconds'} ago.
    </p>
  )
}

function JobView({ id }: { id: string }) {
  const { data, state, error, lastCheckedAt, resume, retry } = usePollTriage(id)
  const seconds = useSecondsSince(lastCheckedAt, state === 'polling' && lastCheckedAt !== null)
  const [recentTarget] = useState(() => loadRecent().find((j) => j.id === id)?.target ?? null)

  if (state === 'not_found') {
    return (
      <>
        <p className="small crumb">
          <Link to="/">Jobs</Link> / {id.slice(0, 8)}
        </p>
        <h1>Job not found</h1>
        <p>
          No job with the ID <code>{id}</code> exists on this server.
        </p>
      </>
    )
  }

  const finding = data?.finding ?? null
  const target = targetOf(data)
  const heading = finding !== null && target !== null ? target : (recentTarget ?? `Job ${id.slice(0, 8)}`)
  const result = finding?.result ?? null
  const hasPath = result !== null && result.path !== null && result.path !== undefined && result.path.length > 0

  return (
    <>
      <p className="small crumb">
        <Link to="/">Jobs</Link> / {id.slice(0, 8)}
      </p>
      <h1 className="mono">{heading}</h1>
      <dl className="facts">
        <dt>Target</dt>
        <dd>{target !== null ? <code>{target}</code> : 'not recorded'}</dd>
        <dt>Status</dt>
        <dd>{data === null ? 'Checking' : statusWord(data.status)}</dd>
        <dt>LLM mode</dt>
        <dd>{llmModeLabel(data?.llm_mode)}</dd>
        <dt>Job ID</dt>
        <dd>
          <code>{id}</code>
        </dd>
      </dl>

      <StatusLine
        data={data}
        state={state}
        errorMessage={error === null ? null : displayPath(error.message)}
        seconds={seconds}
        onResume={resume}
        onRetry={retry}
      />

      {data?.status === 'failed' && (
        <div className="error-summary" role="alert">
          <h2>Job failed</h2>
          <p>
            <RedactedText text={displayPath(data.error ?? 'No error message was recorded.')} />
          </p>
        </div>
      )}

      {data?.status === 'completed' && finding === null && (
        <section className="verdict">
          <div className="reason" role="alert">
            <b>No result returned.</b> The job completed but the server sent no finding.
          </div>
        </section>
      )}

      {finding !== null && result !== null && (
        <>
          <VerdictSection result={result} />
          {hasPath && <PathTable path={result.path ?? []} verdict={result.verdict} />}
          <Commentary rationale={finding.rationale} />
          <ToolTrace calls={finding.tool_calls} />
        </>
      )}
    </>
  )
}

export default function JobPage() {
  const { id } = useParams()
  if (id === undefined || id === '') {
    return (
      <>
        <h1>Job not found</h1>
        <p>No job ID was given.</p>
      </>
    )
  }
  return <JobView key={id} id={id} />
}
