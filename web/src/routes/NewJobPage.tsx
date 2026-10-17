import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { ApiError, createTriage } from '../api/client'
import RecentJobs from '../components/RecentJobs'
import { FIELD_IDS, mapApiDetails, validateForm } from '../lib/formErrors'
import type { FieldErrors, FieldKey, FormValues } from '../lib/formErrors'
import { saveRecent } from '../lib/recentJobs'

interface ErrorState {
  byField: FieldErrors
  formLevel: string[]
}

const EMPTY_VALUES: FormValues = { package: '', version: '', target_module: '', target_symbol: '' }

interface TextFieldProps {
  fieldKey: FieldKey
  label: string
  optional?: boolean
  hint: string
  value: string
  error: string | undefined
  onChange: (key: FieldKey, value: string) => void
}

function TextField({ fieldKey, label, optional, hint, value, error, onChange }: TextFieldProps) {
  const id = FIELD_IDS[fieldKey]
  return (
    <div className={error === undefined ? 'field' : 'field error'}>
      <label htmlFor={id}>
        {label}
        {optional === true ? (
          <>
            {' '}
            <span className="muted optional">(optional)</span>
          </>
        ) : null}
      </label>
      <span className="hint" id={`${id}-hint`}>
        {hint}
      </span>
      {error === undefined ? null : (
        <span className="field-error" id={`${id}-error`}>
          {error}
        </span>
      )}
      <input
        type="text"
        id={id}
        name={fieldKey}
        value={value}
        spellCheck={false}
        autoComplete="off"
        aria-invalid={error === undefined ? undefined : true}
        aria-describedby={error === undefined ? `${id}-hint` : `${id}-hint ${id}-error`}
        onChange={(e) => onChange(fieldKey, e.target.value)}
      />
    </div>
  )
}

const FIELD_ORDER: FieldKey[] = ['package', 'version', 'target_module', 'target_symbol']

export default function NewJobPage() {
  const navigate = useNavigate()
  const [values, setValues] = useState<FormValues>(EMPTY_VALUES)
  const [errors, setErrors] = useState<ErrorState | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const summaryRef = useRef<HTMLDivElement>(null)
  const controllerRef = useRef<AbortController | null>(null)

  useEffect(() => {
    if (errors !== null) summaryRef.current?.focus()
  }, [errors])

  useEffect(
    () => () => {
      controllerRef.current?.abort()
    },
    [],
  )

  const onChange = (key: FieldKey, value: string) => {
    setValues((prev) => ({ ...prev, [key]: value }))
  }

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (submitting) return

    const clientErrors = validateForm(values)
    if (Object.keys(clientErrors).length > 0) {
      setErrors({ byField: clientErrors, formLevel: [] })
      return
    }

    const trimmed = {
      package: values.package.trim(),
      version: values.version.trim(),
      target_module: values.target_module.trim(),
      target_symbol: values.target_symbol.trim(),
    }
    const controller = new AbortController()
    controllerRef.current = controller
    setErrors(null)
    setSubmitting(true)
    try {
      const body = await createTriage(
        {
          package: trimmed.package,
          version: trimmed.version,
          target_module: trimmed.target_module,
          ...(trimmed.target_symbol === '' ? {} : { target_symbol: trimmed.target_symbol }),
        },
        controller.signal,
      )
      saveRecent({
        id: body.id,
        target:
          trimmed.target_symbol === ''
            ? trimmed.target_module
            : `${trimmed.target_module}:${trimmed.target_symbol}`,
        pkg: `${trimmed.package}==${trimmed.version}`,
        submittedAt: new Date().toISOString(),
      })
      void navigate(`/jobs/${encodeURIComponent(body.id)}`)
    } catch (err) {
      if (controller.signal.aborted) return
      setSubmitting(false)
      if (err instanceof ApiError && err.status === 422) {
        const mapped = mapApiDetails(err.details)
        const shown = [...Object.values(mapped.byField), ...mapped.formLevel]
        const formLevel = shown.includes(err.message) ? mapped.formLevel : [...mapped.formLevel, err.message]
        setErrors({ byField: mapped.byField, formLevel })
      } else {
        const message = err instanceof Error ? err.message : 'The request failed.'
        setErrors({ byField: {}, formLevel: [message] })
      }
    }
  }

  const fieldEntries = FIELD_ORDER.flatMap((key) => {
    const message = errors?.byField[key]
    return message === undefined ? [] : [{ key, message }]
  })

  return (
    <>
      {errors === null ? null : (
        <div className="error-summary" role="alert" tabIndex={-1} ref={summaryRef}>
          <h2>There is a problem</h2>
          <ul>
            {fieldEntries.map(({ key, message }) => (
              <li key={key}>
                <a href={`#${FIELD_IDS[key]}`}>{message}</a>
              </li>
            ))}
            {errors.formLevel.map((message, i) => (
              <li key={`form-${i}`}>{message}</li>
            ))}
          </ul>
        </div>
      )}

      <h1>New triage job</h1>
      <p className="lede">
        Downloads the package wheel, builds a call graph, and checks whether the target symbol can be reached from
        an entrypoint.
      </p>

      <form onSubmit={(e) => void onSubmit(e)} noValidate>
        <div className="row2">
          <TextField
            fieldKey="package"
            label="Package"
            hint="As published on PyPI"
            value={values.package}
            error={errors?.byField.package}
            onChange={onChange}
          />
          <TextField
            fieldKey="version"
            label="Version"
            hint="Exact, e.g. 1.0"
            value={values.version}
            error={errors?.byField.version}
            onChange={onChange}
          />
        </div>
        <TextField
          fieldKey="target_module"
          label="Target module"
          hint="Dotted path of the module with the vulnerable code"
          value={values.target_module}
          error={errors?.byField.target_module}
          onChange={onChange}
        />
        <TextField
          fieldKey="target_symbol"
          label="Target symbol"
          optional
          hint="Function or method name. Leave empty to check the whole module."
          value={values.target_symbol}
          error={errors?.byField.target_symbol}
          onChange={onChange}
        />
        <p className="small muted form-note">
          Repository URLs are not supported yet. Only published packages can be checked.
        </p>
        {errors === null
          ? null
          : errors.formLevel.map((message, i) => (
              <p className="field-error" key={`under-${i}`}>
                {message}
              </p>
            ))}
        <button className="primary" type="submit" disabled={submitting}>
          Start triage
        </button>
      </form>

      <RecentJobs />
    </>
  )
}
