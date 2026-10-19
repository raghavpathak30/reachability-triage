import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import notFound from './api/__fixtures__/404-valid-uuid.json'
import App from './App'

afterEach(cleanup)

test('renders the new job form at /', () => {
  render(
    <MemoryRouter initialEntries={['/']}>
      <App />
    </MemoryRouter>,
  )
  expect(screen.getByRole('heading', { name: 'New triage job' })).toBeInTheDocument()
})

test('renders the job view at /jobs/:id', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve(Response.json(notFound.body, { status: notFound.status }))),
  )
  render(
    <MemoryRouter initialEntries={['/jobs/abc']}>
      <App />
    </MemoryRouter>,
  )
  expect(await screen.findByRole('heading', { name: 'Job not found' })).toBeInTheDocument()
  expect(screen.getByText('abc')).toBeInTheDocument()
  vi.unstubAllGlobals()
})
