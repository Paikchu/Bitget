export const DEFAULT_BACKTEST_FORM = {
  days: 90,
  equity: 10000,
  leverage: 5,
  fee_rate: 0.05,
}

const FIELD_TYPES = {
  days: 'integer',
  equity: 'integer',
  leverage: 'integer',
  fee_rate: 'decimal',
}

export function formatBacktestForm(values) {
  return {
    days: String(values.days),
    equity: String(values.equity),
    leverage: String(values.leverage),
    fee_rate: String(values.fee_rate),
  }
}

export function sanitizeBacktestInput(key, value) {
  const fieldType = FIELD_TYPES[key]
  if (!fieldType) {
    return value
  }

  if (fieldType === 'decimal') {
    const cleaned = value.replace(/[^0-9.]/g, '')
    if (!cleaned) return ''

    const [rawInteger = '', ...rest] = cleaned.split('.')
    const fractional = rest.join('')
    const hasDecimalPoint = cleaned.includes('.')
    const integer = normalizeInteger(rawInteger)

    if (!hasDecimalPoint) {
      return integer
    }

    return `${integer || '0'}.${fractional}`
  }

  return normalizeInteger(value.replace(/\D/g, ''))
}

export function parseBacktestForm(form) {
  return {
    days: parseFieldValue('days', form.days),
    equity: parseFieldValue('equity', form.equity),
    leverage: parseFieldValue('leverage', form.leverage),
    fee_rate: parseFieldValue('fee_rate', form.fee_rate),
  }
}

function parseFieldValue(key, rawValue) {
  const fallback = DEFAULT_BACKTEST_FORM[key]
  if (rawValue === '' || rawValue === '0.' || rawValue === '.') {
    return fallback
  }

  const parsed = Number(rawValue)
  return Number.isFinite(parsed) ? parsed : fallback
}

function normalizeInteger(value) {
  if (!value) return ''
  return value.replace(/^0+(?=\d)/, '')
}
