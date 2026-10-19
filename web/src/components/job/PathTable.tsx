import type { components } from '../../api/schema'
import { displayPath, lowConfidenceCount } from '../../lib/displayText'

type CallEdge = components['schemas']['CallEdge']

export default function PathTable({ path, verdict }: { path: CallEdge[]; verdict: string }) {
  const heading = verdict === 'unknown' ? 'Partial path' : 'Call path'
  const low = lowConfidenceCount(path)
  return (
    <>
      <h2>
        {heading}{' '}
        <span className="muted small panel-note">
          · {path.length} {path.length === 1 ? 'edge' : 'edges'}, {low} low confidence
        </span>
      </h2>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Caller</th>
              <th>Callee</th>
              <th>Location</th>
              <th>Confidence</th>
              <th>Rule</th>
            </tr>
          </thead>
          <tbody>
            {path.map((edge, i) => (
              <tr key={i} className={edge.confidence === 'low' ? 'low' : undefined}>
                <td className="num">{i + 1}</td>
                <td>
                  <code>{edge.caller_id}</code>
                </td>
                <td>
                  <code>{edge.callee_id}</code>
                </td>
                <td>
                  <code>
                    {displayPath(edge.file)}:{edge.lineno}
                  </code>
                </td>
                <td className={edge.confidence === 'low' ? 'conf-low' : undefined}>{edge.confidence}</td>
                <td>
                  <code>{edge.resolution_rule}</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
