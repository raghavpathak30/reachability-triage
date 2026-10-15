import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { RECENT_JOBS_KEY } from '../lib/recentJobs'
import RecentJobs from './RecentJobs'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  window.localStorage.clear()
})

function renderPanel() {
  render(
    <MemoryRouter>
      <RecentJobs />
    </MemoryRouter>,
  )
}

test('labels the panel as this browser only and shows an empty note', () => {
  renderPanel()
  expect(screen.getByText(/stored in this browser only/)).toBeInTheDocument()
  expect(screen.getByText('No jobs submitted from this browser yet.')).toBeInTheDocument()
})

test('renders rows with three columns and a link per job', () => {
  window.localStorage.setItem(
    RECENT_JOBS_KEY,
    JSON.stringify([{ id: 'abc', target: 'pkg.sink:vulnerable', pkg: 'x==1.0', submittedAt: '2026-01-01T01:42:00.000Z' }]),
  )
  renderPanel()
  expect(screen.getAllByRole('columnheader').map((c) => c.textContent)).toEqual(['Target', 'Package', 'Submitted'])
  expect(screen.getByRole('link')).toHaveAttribute('href', '/jobs/abc')
  expect(screen.getByText('x==1.0')).toBeInTheDocument()
})

test('encodes the id in the link', () => {
  window.localStorage.setItem(
    RECENT_JOBS_KEY,
    JSON.stringify([{ id: 'a/b?c', target: 't', pkg: 'p', submittedAt: '2026-01-01T01:42:00.000Z' }]),
  )
  renderPanel()
  expect(screen.getByRole('link')).toHaveAttribute('href', '/jobs/a%2Fb%3Fc')
})

test('wrong-shape storage renders an empty list without crashing', () => {
  window.localStorage.setItem(RECENT_JOBS_KEY, '[1,"x",{"id":5}]')
  renderPanel()
  expect(screen.getByText('No jobs submitted from this browser yet.')).toBeInTheDocument()
})

test('renders when storage throws', () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('denied')
  })
  renderPanel()
  expect(screen.getByText(/stored in this browser only/)).toBeInTheDocument()
})
