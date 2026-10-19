import type { components } from '../../api/schema'
import { displayPath } from '../../lib/displayText'
import RedactedText from './RedactedText'

type ReachabilityResult = components['schemas']['ReachabilityResult']

interface VerdictCopy {
  word: string
  className: string
  sentence: string
}

function verdictCopy(verdict: string): VerdictCopy {
  switch (verdict) {
    case 'reachable':
      return {
        word: 'Reachable',
        className: 'value reachable',
        sentence: 'A call path exists from an entrypoint to the target. This dependency likely needs patching.',
      }
    case 'reachable_only_from_tests':
      return {
        word: 'Reachable only from tests',
        className: 'value tests-only',
        sentence: 'The target is called only from test code, not from a production entrypoint.',
      }
    case 'not_reachable':
      return {
        word: 'No path found',
        className: 'value',
        sentence: 'No call path from an entrypoint to the target was found by static analysis.',
      }
    case 'unknown':
      return {
        word: 'Unknown',
        className: 'value',
        sentence: 'The analysis could not decide. Treat this as unresolved, not as safe.',
      }
    default:
      return {
        word: 'Unknown',
        className: 'value',
        sentence: `Unrecognised verdict value: ${verdict}. Treat this as unresolved, not as safe.`,
      }
  }
}

export default function VerdictSection({ result }: { result: ReachabilityResult }) {
  const copy = verdictCopy(result.verdict)
  const hasPath = result.path !== null && result.path !== undefined && result.path.length > 0
  const reason = result.reason ?? null
  return (
    <section className="verdict" aria-labelledby="verdict-label">
      <div className="label" id="verdict-label">
        Verdict
      </div>
      <div className={copy.className}>{copy.word}</div>
      <p>{copy.sentence}</p>
      {reason !== null && (
        <div className="reason">
          <b>Reason.</b> <RedactedText text={displayPath(reason)} />
        </div>
      )}
      {!hasPath && reason === null && (
        <div className="reason" role="alert">
          <b>No evidence returned.</b> This verdict has neither a call path nor a reason, so it cannot be checked.
        </div>
      )}
    </section>
  )
}
