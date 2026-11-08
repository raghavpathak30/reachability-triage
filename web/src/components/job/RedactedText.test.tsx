import { cleanup, render } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import RedactedText from './RedactedText'

afterEach(cleanup)

const LABELS: Array<[string, string]> = [
  ['absolute path', 'Removed by the injection sandbox: an absolute path.'],
  ['token', 'Removed by the injection sandbox: a high-entropy token (API-key or bearer-token shaped).'],
  ['system-prompt-like text', 'Removed by the injection sandbox: system-prompt-like text.'],
  [
    'instruction-like text',
    'Removed by the injection sandbox: instruction-like text (an injection attempt). On jobs before v0.3.2 this marker was used for every redacted category, including absolute paths.',
  ],
]

for (const [label, title] of LABELS) {
  test(`mutes the "${label}" marker with its own tooltip`, () => {
    const { container } = render(<RedactedText text={`before [REDACTED: ${label}] after`} />)
    const marker = container.querySelector('.redacted')
    expect(marker).toHaveTextContent(`[REDACTED: ${label}]`)
    expect(marker).toHaveAttribute('title', title)
  })
}

test('an unknown label gets the generic tooltip', () => {
  const { container } = render(<RedactedText text="x [REDACTED: something new] y" />)
  const marker = container.querySelector('.redacted')
  expect(marker).toHaveTextContent('[REDACTED: something new]')
  expect(marker).toHaveAttribute('title', 'Removed by the injection sandbox.')
})

test('[REDACTED] without a label and org_[REDACTED] are not muted', () => {
  const { container } = render(<RedactedText text="[REDACTED] and org_[REDACTED]" />)
  expect(container.querySelector('.redacted')).toBeNull()
  expect(container).toHaveTextContent('[REDACTED] and org_[REDACTED]')
})

test('two markers in one string are both muted', () => {
  const { container } = render(<RedactedText text="a [REDACTED: token] b [REDACTED: absolute path] c" />)
  const marked = container.querySelectorAll('.redacted')
  expect(marked).toHaveLength(2)
  expect(marked[0]).toHaveTextContent('[REDACTED: token]')
  expect(marked[1]).toHaveTextContent('[REDACTED: absolute path]')
})
