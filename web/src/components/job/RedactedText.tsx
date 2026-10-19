import { splitRedactions } from '../../lib/displayText'

export const REDACTION_TITLE = 'Removed by the injection sandbox. Absolute paths are always redacted.'

export default function RedactedText({ text }: { text: string }) {
  return (
    <>
      {splitRedactions(text).map((part, i) =>
        part.redacted ? (
          <span key={i} className="redacted" title={REDACTION_TITLE}>
            {part.text}
          </span>
        ) : (
          <span key={i}>{part.text}</span>
        ),
      )}
    </>
  )
}
