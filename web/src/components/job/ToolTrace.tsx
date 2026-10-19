import type { components } from '../../api/schema'
import { groupToolCalls } from '../../lib/displayText'
import RedactedText from './RedactedText'

type ToolCallRecord = components['schemas']['ToolCallRecord']

export default function ToolTrace({ calls }: { calls: ToolCallRecord[] | null | undefined }) {
  const groups = groupToolCalls(calls)
  if (groups.length === 0) return null
  const total = groups.reduce((n, g) => n + g.count, 0)
  return (
    <details>
      <summary>Tool calls ({total})</summary>
      {groups.map((g, i) => {
        const range =
          g.count === 1 ? `${g.first.sequence}` : `${g.first.sequence}–${g.last.sequence}`
        return (
          <div className="call" key={`${g.first.sequence}-${i}`}>
            <div className="call-head">
              <span className="muted">{range}</span>
              <code>{g.first.tool_name}</code>
              {g.count > 1 && <span className="repeat">same call {g.count} times in a row</span>}
            </div>
            <pre className="args">
              <RedactedText text={JSON.stringify(g.first.arguments, null, 2)} />
            </pre>
            <pre>
              <RedactedText text={g.first.result} />
            </pre>
          </div>
        )
      })}
    </details>
  )
}
