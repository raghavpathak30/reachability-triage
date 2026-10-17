import { useCallback, useEffect, useState } from 'react'
import { ApiError, getTriage, NetworkError } from '../api/client'
import type { TriageOut } from '../api/client'

export type PollState = 'polling' | 'done' | 'capped' | 'not_found' | 'error'

export const INITIAL_DELAY_MS = 1000
export const MAX_DELAY_MS = 5000
export const CAP_MS = 10 * 60 * 1000
export const MAX_CONSECUTIVE_FAILURES = 3

export function nextDelay(prev: number): number {
  return Math.min(MAX_DELAY_MS, prev * 1.5)
}

interface PollRecord {
  id: string
  data: TriageOut | null
  state: PollState
  error: Error | null
  failureCount: number
  lastCheckedAt: number | null
}

export interface UsePollTriage {
  data: TriageOut | null
  state: PollState
  error: Error | null
  failureCount: number
  lastCheckedAt: number | null
  resume: () => void
  retry: () => void
}

function initialRecord(id: string): PollRecord {
  return { id, data: null, state: 'polling', error: null, failureCount: 0, lastCheckedAt: null }
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function isTransient(error: unknown): boolean {
  if (error instanceof NetworkError) return true
  if (error instanceof ApiError) {
    return (
      error.status >= 500 || error.code === 'UNPARSEABLE_RESPONSE' || error.code === 'UNPARSEABLE_ERROR'
    )
  }
  return false
}

export function usePollTriage(id: string): UsePollTriage {
  const [record, setRecord] = useState<PollRecord>(() => initialRecord(id))
  const [runId, setRunId] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    const startedAt = Date.now()
    let stopped = false
    let failures = 0
    let delay = INITIAL_DELAY_MS
    let pollTimer: ReturnType<typeof setTimeout> | undefined

    const update = (patch: Partial<PollRecord>) => {
      setRecord((prev) => ({ ...(prev.id === id ? prev : initialRecord(id)), ...patch, id }))
    }

    const halt = () => {
      stopped = true
      clearTimeout(pollTimer)
      clearTimeout(deadlineTimer)
    }

    const deadlineTimer = setTimeout(() => {
      stopped = true
      clearTimeout(pollTimer)
      controller.abort()
      update({ state: 'capped', error: null })
    }, CAP_MS)

    const scheduleNext = () => {
      const remaining = CAP_MS - (Date.now() - startedAt)
      if (remaining <= 0) return
      pollTimer = setTimeout(() => void poll(), Math.min(delay, remaining))
      delay = nextDelay(delay)
    }

    const poll = async (): Promise<void> => {
      if (stopped) return
      try {
        const data = await getTriage(id, controller.signal)
        if (stopped) return
        failures = 0
        const terminal = data.status === 'completed' || data.status === 'failed'
        update({
          data,
          state: terminal ? 'done' : 'polling',
          error: null,
          failureCount: 0,
          lastCheckedAt: Date.now(),
        })
        if (terminal) {
          halt()
          return
        }
      } catch (err) {
        if (stopped || isAbort(err)) return
        const error = err instanceof Error ? err : new Error('Polling failed')
        if (err instanceof ApiError && err.status === 404 && err.code === 'NOT_FOUND') {
          halt()
          update({ state: 'not_found', error })
          return
        }
        if (!isTransient(err)) {
          halt()
          update({ state: 'error', error })
          return
        }
        failures += 1
        if (failures >= MAX_CONSECUTIVE_FAILURES) {
          halt()
          update({ state: 'error', error, failureCount: failures })
          return
        }
        update({ error, failureCount: failures })
      }
      scheduleNext()
    }

    void poll()

    return () => {
      halt()
      controller.abort()
    }
  }, [id, runId])

  const restart = useCallback(() => {
    setRecord((prev) => ({ ...prev, state: 'polling', error: null, failureCount: 0 }))
    setRunId((n) => n + 1)
  }, [])

  const current = record.id === id ? record : initialRecord(id)
  return {
    data: current.data,
    state: current.state,
    error: current.error,
    failureCount: current.failureCount,
    lastCheckedAt: current.lastCheckedAt,
    resume: restart,
    retry: restart,
  }
}
