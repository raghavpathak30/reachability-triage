import { useParams } from 'react-router'

export default function JobPage() {
  const { id } = useParams()
  return (
    <>
      <h1>Job</h1>
      <p>
        <code>{id}</code>
      </p>
    </>
  )
}
