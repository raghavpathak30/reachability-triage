import { afterEach, expect, test, vi } from 'vitest'
import { loadRecent, RECENT_JOBS_KEY, saveRecent } from './recentJobs'

afterEach(() => {
  vi.restoreAllMocks()
  window.localStorage.clear()
})

function entry(n: number) {
  return { id: `id-${n}`, target: `m${n}:f`, pkg: `p==${n}`, submittedAt: new Date(2026, 0, 1, 0, n).toISOString() }
}

test('empty storage gives an empty list', () => {
  expect(loadRecent()).toEqual([])
})

test('newest first and dedupe by id', () => {
  saveRecent(entry(1))
  saveRecent(entry(2))
  saveRecent(entry(1))
  expect(loadRecent().map((e) => e.id)).toEqual(['id-1', 'id-2'])
})

test('keeps at most 20 entries', () => {
  for (let i = 0; i < 25; i++) saveRecent(entry(i))
  const list = loadRecent()
  expect(list).toHaveLength(20)
  expect(list[0]?.id).toBe('id-24')
})

test('malformed JSON gives an empty list', () => {
  window.localStorage.setItem(RECENT_JOBS_KEY, '{not json')
  expect(loadRecent()).toEqual([])
})

test.each(['{}', 'null', '"x"', '5'])('valid JSON of wrong shape %s gives an empty list', (raw) => {
  window.localStorage.setItem(RECENT_JOBS_KEY, raw)
  expect(loadRecent()).toEqual([])
})

test('invalid entries are dropped individually', () => {
  const good = entry(1)
  window.localStorage.setItem(
    RECENT_JOBS_KEY,
    JSON.stringify([1, 'x', { id: 5 }, { ...good, submittedAt: 'not a date' }, good]),
  )
  expect(loadRecent()).toEqual([good])
})

test('throwing getItem and setItem do not throw', () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('denied')
  })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('denied')
  })
  expect(loadRecent()).toEqual([])
  expect(() => saveRecent(entry(1))).not.toThrow()
})
