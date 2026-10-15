import type { components } from './schema'

export type TriageOut = components['schemas']['TriageOut']

export interface CreateTriageBody {
  package: string
  version: string
  target_module: string
  target_symbol?: string
}

export interface ErrorDetail {
  loc: (string | number)[]
  msg: string
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details?: ErrorDetail[]

  constructor(status: number, code: string, message: string, details?: ErrorDetail[]) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    if (details !== undefined) {
      this.details = details
    }
  }
}

export class NetworkError extends Error {
  constructor(message: string, options?: { cause?: unknown }) {
    super(message, options)
    this.name = 'NetworkError'
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function isDetail(value: unknown): value is ErrorDetail {
  return (
    isRecord(value) &&
    Array.isArray(value.loc) &&
    value.loc.every((p) => typeof p === 'string' || typeof p === 'number') &&
    typeof value.msg === 'string'
  )
}

async function toApiError(response: Response): Promise<ApiError> {
  let parsed: unknown
  try {
    parsed = await response.json()
  } catch {
    parsed = undefined
  }
  if (isRecord(parsed) && isRecord(parsed.error)) {
    const { code, message, details } = parsed.error
    if (typeof code === 'string' && typeof message === 'string') {
      const validDetails =
        Array.isArray(details) && details.every(isDetail) ? details : undefined
      return new ApiError(response.status, code, message, validDetails)
    }
  }
  return new ApiError(
    response.status,
    'UNPARSEABLE_ERROR',
    `Unexpected response (HTTP ${response.status})`,
  )
}

async function request<T>(path: string, init: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') {
      throw cause
    }
    throw new NetworkError('Network request failed', { cause })
  }
  if (!response.ok) {
    throw await toApiError(response)
  }
  return (await response.json()) as T
}

export function getHealth(signal?: AbortSignal): Promise<{ status: string }> {
  return request<{ status: string }>('/healthz', { signal })
}

export function createTriage(body: CreateTriageBody, signal?: AbortSignal): Promise<TriageOut> {
  const { package: pkg, version, target_module, target_symbol } = body
  const payload: CreateTriageBody = { package: pkg, version, target_module }
  if (target_symbol !== undefined) {
    payload.target_symbol = target_symbol
  }
  return request<TriageOut>('/v1/triage', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  })
}

export function getTriage(id: string, signal?: AbortSignal): Promise<TriageOut> {
  return request<TriageOut>(`/v1/triage/${encodeURIComponent(id)}`, { signal })
}
