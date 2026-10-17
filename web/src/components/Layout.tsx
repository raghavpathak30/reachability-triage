import { Link, Outlet } from 'react-router'

export default function Layout() {
  return (
    <>
      <header className="bar">
        <div className="wrap">
          <Link className="name" to="/">
            reachability-triage
          </Link>
          <nav>
            <Link to="/">New job</Link>
            <a href="https://github.com/raghavpathak30/reachability-triage">Source on GitHub</a>
          </nav>
          <span className="env">local instance · no auth</span>
        </div>
      </header>
      <main className="wrap">
        <Outlet />
      </main>
      <footer>
        <div className="wrap">
          reachability-triage · local instance · verdicts come from static analysis; the agent only
          chooses which queries to run
        </div>
      </footer>
    </>
  )
}
