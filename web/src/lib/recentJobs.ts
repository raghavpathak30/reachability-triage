export interface RecentJob {
  id: string
  target: string
  pkg: string
  submittedAt: string
}

export const RECENT_JOBS_KEY = 'reachability.recentJobs.v1'
export const RECENT_JOBS_MAX = 20

function isRecentJob(value: unknown): value is RecentJob {
  if (typeof value !== 'object' || value === null) return false
  const v = value as Record<string, unknown>
  return (
    typeof v.id === 'string' &&
    typeof v.target === 'string' &&
    typeof v.pkg === 'string' &&
    typeof v.submittedAt === 'string' &&
    !Number.isNaN(new Date(v.submittedAt).getTime())
  )
}

export function loadRecent(): RecentJob[] {
  try {
    const raw = window.localStorage.getItem(RECENT_JOBS_KEY)
    if (raw === null) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter(isRecentJob).slice(0, RECENT_JOBS_MAX)
  } catch {
    return []
  }
}

export function saveRecent(entry: RecentJob): void {
  try {
    const next = [entry, ...loadRecent().filter((e) => e.id !== entry.id)].slice(0, RECENT_JOBS_MAX)
    window.localStorage.setItem(RECENT_JOBS_KEY, JSON.stringify(next))
  } catch {
    // Storage unavailable: recent jobs are optional, the submit flow continues.
  }
}
