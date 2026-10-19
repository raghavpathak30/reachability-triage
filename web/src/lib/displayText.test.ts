import { expect, test } from 'vitest'
import type { components } from '../api/schema'
import { displayArguments, displayPath, groupToolCalls, llmModeLabel, lowConfidenceCount, splitRedactions } from './displayText'

type Call = components['schemas']['ToolCallRecord']

function call(sequence: number, tool_name: string, args: Record<string, unknown>, result = ''): Call {
  return { sequence, tool_name, arguments: args, result }
}

test('R9 shows file paths after /extracted/', () => {
  expect(displayPath('/tmp/reachability-triage-abc/extracted/pkg/entry.py')).toBe('pkg/entry.py')
})

test('R9 replaces a mid-sentence path', () => {
  expect(displayPath('opaque call at /tmp/x_1/extracted/pkg/entry.py:5 carries no name')).toBe(
    'opaque call at pkg/entry.py:5 carries no name',
  )
})

test('R9 leaves a string without the marker unchanged', () => {
  expect(displayPath('/tmp/other/pkg/entry.py')).toBe('/tmp/other/pkg/entry.py')
})

test('R9 keeps leading quotes and brackets', () => {
  expect(displayPath("('/tmp/x/extracted/a.py')")).toBe("('a.py')")
  expect(displayPath('"file": "/tmp/x/extracted/a.py"')).toBe('"file": "a.py"')
})

test('R9 leaves relative paths untouched', () => {
  expect(displayPath('see pkg/extracted/helpers.py')).toBe('see pkg/extracted/helpers.py')
})

test('R9 handles a very long token without hanging', () => {
  const long = '/' + 'a'.repeat(50000)
  expect(displayPath(long)).toBe(long)
})

test('R9 splits the redaction marker into a redacted part', () => {
  expect(splitRedactions("PosixPath('[REDACTED: instruction-like text]'), x")).toEqual([
    { text: "PosixPath('", redacted: false },
    { text: '[REDACTED: instruction-like text]', redacted: true },
    { text: "'), x", redacted: false },
  ])
  expect(splitRedactions('plain')).toEqual([{ text: 'plain', redacted: false }])
  expect(splitRedactions('')).toEqual([])
})

test('R6 groups same-tool same-arguments calls and shows a sequence range', () => {
  const groups = groupToolCalls([
    call(1, 'find_callers', { node_id: 'a' }, 'first'),
    call(2, 'find_callers', { node_id: 'a' }, 'second'),
    call(3, 'find_callers', { node_id: 'a' }, 'third'),
  ])
  expect(groups).toHaveLength(1)
  expect(groups[0]?.count).toBe(3)
  expect(groups[0]?.first.sequence).toBe(1)
  expect(groups[0]?.last.sequence).toBe(3)
  expect(groups[0]?.first.result).toBe('first')
})

test('R6 does not group three find_callers calls on different node_ids', () => {
  const groups = groupToolCalls([
    call(1, 'find_callers', { node_id: 'a' }),
    call(2, 'find_callers', { node_id: 'b' }),
    call(3, 'find_callers', { node_id: 'c' }),
  ])
  expect(groups.map((g) => g.count)).toEqual([1, 1, 1])
})

test('R6 non-consecutive identical calls do not group', () => {
  const groups = groupToolCalls([
    call(1, 'find_callers', { node_id: 'a' }),
    call(2, 'find_callers', { node_id: 'b' }),
    call(3, 'find_callers', { node_id: 'a' }),
  ])
  expect(groups).toHaveLength(3)
})

test('R6 argument key order does not matter', () => {
  const groups = groupToolCalls([call(1, 't', { a: 1, b: { y: 1, x: 2 } }), call(2, 't', { b: { x: 2, y: 1 }, a: 1 })])
  expect(groups).toHaveLength(1)
  expect(groups[0]?.count).toBe(2)
})

test('R6 sorts a copy by sequence and does not mutate the input', () => {
  const input = [call(2, 't', { a: 2 }), call(0, 't', { a: 0 }), call(1, 't', { a: 1 })]
  const groups = groupToolCalls(input)
  expect(groups.map((g) => g.first.sequence)).toEqual([0, 1, 2])
  expect(input.map((c) => c.sequence)).toEqual([2, 0, 1])
})

test('R6 empty or missing trace gives no groups', () => {
  expect(groupToolCalls([])).toEqual([])
  expect(groupToolCalls(undefined)).toEqual([])
  expect(groupToolCalls(null)).toEqual([])
})

test('counts low confidence edges and labels llm mode', () => {
  const edge = (confidence: 'high' | 'low') => ({
    caller_id: 'a',
    callee_id: 'b',
    confidence,
    file: 'f',
    lineno: 1,
    resolution_rule: 'module_scope' as const,
  })
  expect(lowConfidenceCount([edge('high'), edge('low'), edge('low')])).toBe(2)
  expect(lowConfidenceCount(null)).toBe(0)
  expect(llmModeLabel('stub')).toBe('stub (deterministic policy, not a real model)')
  expect(llmModeLabel('groq')).toBe('groq')
  expect(llmModeLabel(null)).toBe('not recorded')
  expect(llmModeLabel(undefined)).toBe('not recorded')
})

test('R9 displayArguments trims string values structurally and keeps every key', () => {
  const out = displayArguments({ a: '/x', file: '/tmp/q/extracted/a.py', list: ['/home/u/x', '/tmp/q/extracted/b.py'] })
  expect(JSON.parse(out)).toEqual({ a: '/x', file: 'a.py', list: ['/home/u/x', 'b.py'] })
  expect(out).toContain('\n')
})
