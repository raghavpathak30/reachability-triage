import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import empty from '../api/__fixtures__/422-empty-body.json'
import missing from '../api/__fixtures__/422-missing-version.json'
import { loadRecent } from '../lib/recentJobs'
import NewJobPage from './NewJobPage'

const ID = '057692ae-ca68-4ae7-b6a7-551dda6e3db5'

beforeEach(() => {
  window.localStorage.clear()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<NewJobPage />} />
        <Route path="/jobs/:id" element={<p>placeholder for job</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

function stubFetch(status: number, body: unknown) {
  const fetchMock = vi.fn(() => Promise.resolve(Response.json(body, { status })))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function fill(label: string | RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } })
}

function fillAll() {
  fill('Package', ' reach-fx-02 ')
  fill('Version', '1.0')
  fill('Target module', 'pkg.sink')
  fill(/Target symbol/, 'vulnerable')
}

test('empty submit shows a summary and field errors without calling fetch', () => {
  const fetchMock = stubFetch(202, {})
  renderPage()
  fireEvent.click(screen.getByRole('button', { name: 'Start triage' }))
  const alert = screen.getByRole('alert')
  expect(alert).toHaveTextContent('There is a problem')
  expect(alert).toHaveFocus()
  expect(alert.querySelector('a[href="#module"]')).not.toBeNull()
  expect(alert.querySelector('a[href="#package"]')).not.toBeNull()
  expect(alert.querySelector('a[href="#version"]')).not.toBeNull()
  expect(document.querySelectorAll('.field.error')).toHaveLength(3)
  expect(fetchMock).not.toHaveBeenCalled()
})

test('rendered page has no inline style attributes', () => {
  renderPage()
  expect(document.querySelector('[style]')).toBeNull()
})

test('success trims values, saves the job and navigates to /jobs/:id', async () => {
  const fetchMock = stubFetch(202, { id: ID, status: 'queued' })
  renderPage()
  fillAll()
  fireEvent.click(screen.getByRole('button', { name: 'Start triage' }))
  await screen.findByText('placeholder for job')
  const init = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
  expect(JSON.parse(init[1].body as string)).toEqual({
    package: 'reach-fx-02',
    version: '1.0',
    target_module: 'pkg.sink',
    target_symbol: 'vulnerable',
  })
  expect(loadRecent()).toMatchObject([{ id: ID, target: 'pkg.sink:vulnerable', pkg: 'reach-fx-02==1.0' }])
})

test('server 422 maps target_module to the module field', async () => {
  stubFetch(empty.status, empty.body)
  renderPage()
  fillAll()
  fireEvent.click(screen.getByRole('button', { name: 'Start triage' }))
  const alert = await screen.findByRole('alert')
  expect(alert.querySelector('a[href="#module"]')).toHaveTextContent('Field required')
  expect(document.querySelector('.field.error #module')).not.toBeNull()
  expect(alert.querySelectorAll('li')).toHaveLength(1)
})

test('server 422 with a ["body"] loc shows form-level text', async () => {
  stubFetch(missing.status, missing.body)
  renderPage()
  fillAll()
  fireEvent.click(screen.getByRole('button', { name: 'Start triage' }))
  const alert = await screen.findByRole('alert')
  expect(alert.querySelector('a')).toBeNull()
  expect(alert).toHaveTextContent('Both')
  expect(document.querySelectorAll('.field.error')).toHaveLength(0)
  expect(screen.getAllByText(/Both 'package' and 'version'/).length).toBeGreaterThanOrEqual(2)
})

test('network failure shows a form-level message and re-enables the button', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('offline'))))
  renderPage()
  fillAll()
  fireEvent.click(screen.getByRole('button', { name: 'Start triage' }))
  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('Network request failed')
  await waitFor(() => expect(screen.getByRole('button', { name: 'Start triage' })).toBeEnabled())
})

test('mentions that repository URLs are not supported', () => {
  renderPage()
  expect(screen.getByText(/Repository URLs are not supported yet/)).toBeInTheDocument()
})
