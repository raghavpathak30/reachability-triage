import type { components } from '../api/schema'

type ToolCallRecord = components['schemas']['ToolCallRecord']
type CallEdge = components['schemas']['CallEdge']

export const REDACTION_MARKER = '[REDACTED: instruction-like text]'

// An absolute path token (starts at a "/" that follows start, whitespace or an
// opening quote/bracket) up to and including the first "/extracted/". The token
// length is bounded to avoid quadratic backtracking on long untrusted input.
const EXTRACTED_PATH = /(?<=^|[\s'"([{<])(?=\/)[^\s]{0,512}?\/extracted\//g

export function displayPath(s: string): string {
  return s.replace(EXTRACTED_PATH, '')
}

function mapStrings(value: unknown): unknown {
  if (typeof value === 'string') return displayPath(value)
  if (Array.isArray(value)) return value.map(mapStrings)
  if (typeof value === 'object' && value !== null) {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, mapStrings(v)]))
  }
  return value
}

// Trims string values structurally, then serialises; never trims serialised text.
export function displayArguments(args: unknown): string {
  return JSON.stringify(mapStrings(args), null, 2) ?? 'null'
}

export interface TextPart {
  text: string
  redacted: boolean
}

export function splitRedactions(s: string): TextPart[] {
  const parts: TextPart[] = []
  const pieces = s.split(REDACTION_MARKER)
  pieces.forEach((piece, i) => {
    if (piece !== '') parts.push({ text: piece, redacted: false })
    if (i < pieces.length - 1) parts.push({ text: REDACTION_MARKER, redacted: true })
  })
  return parts
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  if (typeof value === 'object' && value !== null) {
    const obj = value as Record<string, unknown>
    const keys = Object.keys(obj).sort()
    return `{${keys.map((k) => `${JSON.stringify(k)}:${canonical(obj[k])}`).join(',')}}`
  }
  return JSON.stringify(value) ?? 'null'
}

export interface ToolCallGroup {
  first: ToolCallRecord
  last: ToolCallRecord
  count: number
}

export function groupToolCalls(calls: ToolCallRecord[] | null | undefined): ToolCallGroup[] {
  if (calls === undefined || calls === null) return []
  const sorted = [...calls].sort((a, b) => a.sequence - b.sequence)
  const groups: ToolCallGroup[] = []
  let key: string | null = null
  for (const call of sorted) {
    const callKey = `${call.tool_name}\u0000${canonical(call.arguments)}`
    const current = groups[groups.length - 1]
    if (current !== undefined && callKey === key) {
      current.last = call
      current.count += 1
    } else {
      groups.push({ first: call, last: call, count: 1 })
      key = callKey
    }
  }
  return groups
}

export function lowConfidenceCount(path: CallEdge[] | null | undefined): number {
  if (path === undefined || path === null) return 0
  return path.filter((e) => e.confidence === 'low').length
}

export function llmModeLabel(mode: string | null | undefined): string {
  if (mode === 'stub') return 'stub (deterministic policy, not a real model)'
  if (mode === 'groq') return 'groq'
  if (mode === null || mode === undefined) return 'not recorded'
  return mode
}
