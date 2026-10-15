import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import App from './App'

afterEach(cleanup)

test('renders the heading', () => {
  render(<App />)
  expect(screen.getByRole('heading', { name: 'Reachability triage' })).toBeInTheDocument()
})
