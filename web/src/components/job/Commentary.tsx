import RedactedText from './RedactedText'

export default function Commentary({ rationale }: { rationale: string }) {
  return (
    <>
      <h2>Agent commentary</h2>
      <p className="note">Written by the agent to explain its steps. Not used to derive the verdict.</p>
      <blockquote className="commentary">
        <RedactedText text={rationale} />
      </blockquote>
    </>
  )
}
