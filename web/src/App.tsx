import { Route, Routes } from 'react-router'
import Layout from './components/Layout'
import JobPage from './routes/JobPage'
import NewJobPage from './routes/NewJobPage'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<NewJobPage />} />
        <Route path="/jobs/:id" element={<JobPage />} />
      </Route>
    </Routes>
  )
}
