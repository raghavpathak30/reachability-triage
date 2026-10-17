import { expect, test } from 'vitest'
import empty from '../api/__fixtures__/422-empty-body.json'
import missing from '../api/__fixtures__/422-missing-version.json'
import { FIELD_IDS, mapApiDetails, validateForm } from './formErrors'

const valid = { package: 'p', version: '1', target_module: 'm', target_symbol: '' }

test('validateForm accepts a complete form with empty symbol', () => {
  expect(validateForm(valid)).toEqual({})
})

test('validateForm requires package, version, target_module after trimming', () => {
  const errors = validateForm({ package: ' ', version: '', target_module: '\t', target_symbol: '' })
  expect(Object.keys(errors).sort()).toEqual(['package', 'target_module', 'version'])
})

test('FIELD_IDS maps API keys to reference ids', () => {
  expect(FIELD_IDS.target_module).toBe('module')
  expect(FIELD_IDS.target_symbol).toBe('symbol')
})

test('mapApiDetails maps body.target_module to a field error', () => {
  const mapped = mapApiDetails(empty.body.error.details)
  expect(mapped.byField).toEqual({ target_module: 'Field required' })
  expect(mapped.formLevel).toEqual([])
})

test('mapApiDetails keeps a ["body"] loc as form-level text', () => {
  const mapped = mapApiDetails(missing.body.error.details)
  expect(mapped.byField).toEqual({})
  expect(mapped.formLevel).toHaveLength(1)
  expect(mapped.formLevel[0]).toContain('Both')
})

test('mapApiDetails sends unknown keys to form-level and tolerates undefined', () => {
  const mapped = mapApiDetails([{ loc: ['body', 'repo_url'], msg: 'bad' }])
  expect(mapped.formLevel).toEqual(['bad'])
  expect(mapApiDetails(undefined)).toEqual({ byField: {}, formLevel: [] })
})
