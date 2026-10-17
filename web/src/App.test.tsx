import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test } from 'vitest'
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

test('renders the job placeholder at /jobs/:id', () => {
  render(
    <MemoryRouter initialEntries={['/jobs/abc']}>
      <App />
    </MemoryRouter>,
  )
  expect(screen.getByRole('heading', { name: 'Job' })).toBeInTheDocument()
  expect(screen.getByText('abc')).toBeInTheDocument()
})
