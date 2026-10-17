import type { ErrorDetail } from '../api/client'

export type FieldKey = 'package' | 'version' | 'target_module' | 'target_symbol'

export type FieldErrors = Partial<Record<FieldKey, string>>

export interface FormValues {
  package: string
  version: string
  target_module: string
  target_symbol: string
}

export const FIELD_IDS: Record<FieldKey, string> = {
  package: 'package',
  version: 'version',
  target_module: 'module',
  target_symbol: 'symbol',
}

const REQUIRED_MESSAGES: Record<'package' | 'version' | 'target_module', string> = {
  package: 'Enter a package',
  version: 'Enter a version',
  target_module: 'Enter a target module',
}

export function validateForm(values: FormValues): FieldErrors {
  const errors: FieldErrors = {}
  if (values.package.trim() === '') errors.package = REQUIRED_MESSAGES.package
  if (values.version.trim() === '') errors.version = REQUIRED_MESSAGES.version
  if (values.target_module.trim() === '') errors.target_module = REQUIRED_MESSAGES.target_module
  return errors
}

function isFieldKey(value: unknown): value is FieldKey {
  return typeof value === 'string' && Object.prototype.hasOwnProperty.call(FIELD_IDS, value)
}

export interface MappedDetails {
  byField: FieldErrors
  formLevel: string[]
}

export function mapApiDetails(details: ErrorDetail[] | undefined): MappedDetails {
  const byField: FieldErrors = {}
  const formLevel: string[] = []
  for (const detail of details ?? []) {
    const [root, key] = detail.loc
    if (detail.loc.length === 2 && root === 'body' && isFieldKey(key)) {
      if (byField[key] === undefined) {
        byField[key] = detail.msg
      }
    } else {
      formLevel.push(detail.msg)
    }
  }
  return { byField, formLevel }
}
