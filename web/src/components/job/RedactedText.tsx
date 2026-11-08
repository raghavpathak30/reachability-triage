import { redactionLabel, splitRedactions } from '../../lib/displayText'

const PREFIX = 'Removed by the injection sandbox'

export function redactionTitle(label: string | null): string {
  switch (label) {
    case 'absolute path':
      return `${PREFIX}: an absolute path.`
    case 'token':
      return `${PREFIX}: a high-entropy token (API-key or bearer-token shaped).`
    case 'system-prompt-like text':
      return `${PREFIX}: system-prompt-like text.`
    case 'instruction-like text':
      return `${PREFIX}: instruction-like text (an injection attempt). On jobs before v0.3.2 this marker was used for every redacted category, including absolute paths.`
    default:
      return `${PREFIX}.`
  }
}

export default function RedactedText({ text }: { text: string }) {
  return (
    <>
      {splitRedactions(text).map((part, i) =>
        part.redacted ? (
          <span key={i} className="redacted" title={redactionTitle(redactionLabel(part.text))}>
            {part.text}
          </span>
        ) : (
          <span key={i}>{part.text}</span>
        ),
      )}
    </>
  )
}
