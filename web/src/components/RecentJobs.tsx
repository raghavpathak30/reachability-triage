import { Link } from 'react-router'
import { loadRecent } from '../lib/recentJobs'

export default function RecentJobs() {
  const jobs = loadRecent()
  return (
    <section>
      <h2>
        Recent jobs <span className="muted small panel-note">· stored in this browser only</span>
      </h2>
      {jobs.length === 0 ? (
        <p className="muted">No jobs submitted from this browser yet.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Target</th>
                <th>Package</th>
                <th>Submitted</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.id}>
                  <td>
                    <Link to={`/jobs/${encodeURIComponent(job.id)}`}>
                      <code>{job.target}</code>
                    </Link>
                  </td>
                  <td>
                    <code>{job.pkg}</code>
                  </td>
                  <td>
                    <time dateTime={job.submittedAt}>{new Date(job.submittedAt).toLocaleString()}</time>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
