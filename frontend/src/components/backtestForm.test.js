import test from 'node:test'
import assert from 'node:assert/strict'

import {
  sanitizeBacktestInput,
  parseBacktestForm,
  formatBacktestForm,
  DEFAULT_BACKTEST_FORM,
} from './backtestForm.js'

test('sanitizeBacktestInput removes redundant leading zeros for integer fields', () => {
  assert.equal(sanitizeBacktestInput('days', '0100'), '100')
  assert.equal(sanitizeBacktestInput('days', '100'), '100')
  assert.equal(sanitizeBacktestInput('days', '10'), '10')
  assert.equal(sanitizeBacktestInput('days', '0'), '0')
})

test('sanitizeBacktestInput keeps decimal typing stable for fee rate', () => {
  assert.equal(sanitizeBacktestInput('fee_rate', '00.05'), '0.05')
  assert.equal(sanitizeBacktestInput('fee_rate', '0.050'), '0.050')
  assert.equal(sanitizeBacktestInput('fee_rate', '.'), '0.')
})

test('parseBacktestForm returns numbers and falls back on invalid blanks', () => {
  const parsed = parseBacktestForm({
    timeframe: '4h',
    days: '100',
    equity: '',
    leverage: '7',
    fee_rate: '0.05',
  })

  assert.deepEqual(parsed, {
    timeframe: '4h',
    days: 100,
    equity: DEFAULT_BACKTEST_FORM.equity,
    leverage: 7,
    fee_rate: 0.05,
  })
})

test('formatBacktestForm turns numeric defaults into controlled string values', () => {
  assert.deepEqual(formatBacktestForm(DEFAULT_BACKTEST_FORM), {
    timeframe: '15m',
    days: '90',
    equity: '10000',
    leverage: '5',
    fee_rate: '0.05',
  })
})

test('parseBacktestForm falls back to default timeframe when empty', () => {
  const parsed = parseBacktestForm({
    timeframe: '',
    days: '90',
    equity: '10000',
    leverage: '5',
    fee_rate: '0.05',
  })

  assert.equal(parsed.timeframe, DEFAULT_BACKTEST_FORM.timeframe)
})
