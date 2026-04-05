import { useEffect, useMemo, useState } from 'react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import dayjs from 'dayjs'
import useBotStore from '../store/botStore'
import {
  DEFAULT_BACKTEST_FORM,
  formatBacktestForm,
  parseBacktestForm,
  sanitizeBacktestInput,
} from './backtestForm'

const BACKTEST_DEFAULTS_KEY = 'bitget-backtest-defaults'
const TIMEFRAME_OPTIONS = ['1m', '5m', '15m', '30m', '1h', '4h', '1d']

function formatRunParam(value) {
  if (typeof value === 'boolean') return value ? '开' : '关'
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

function loadBacktestDefaults() {
  if (typeof window === 'undefined') return formatBacktestForm(DEFAULT_BACKTEST_FORM)

  try {
    const raw = window.localStorage.getItem(BACKTEST_DEFAULTS_KEY)
    if (!raw) return formatBacktestForm(DEFAULT_BACKTEST_FORM)
    const parsed = JSON.parse(raw)
    return formatBacktestForm({
      timeframe: TIMEFRAME_OPTIONS.includes(parsed.timeframe)
        ? parsed.timeframe
        : DEFAULT_BACKTEST_FORM.timeframe,
      days: Number(parsed.days) || DEFAULT_BACKTEST_FORM.days,
      equity: Number(parsed.equity) || DEFAULT_BACKTEST_FORM.equity,
      leverage: Number(parsed.leverage) || DEFAULT_BACKTEST_FORM.leverage,
      fee_rate: Number(parsed.fee_rate) || DEFAULT_BACKTEST_FORM.fee_rate,
    })
  } catch {
    return formatBacktestForm(DEFAULT_BACKTEST_FORM)
  }
}

export default function BacktestPanel({ timeframe, onTimeframeChange }) {
  const [form, setForm] = useState(loadBacktestDefaults)
  const [status, setStatus] = useState(null)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [savedNotice, setSavedNotice] = useState(null)
  const setBacktestResult = useBotStore((s) => s.setBacktestResult)
  const setBacktestFeedback = useBotStore((s) => s.setBacktestFeedback)
  const setBacktestFeedbackStatus = useBotStore((s) => s.setBacktestFeedbackStatus)
  const strategyCode = useBotStore((s) => s.strategyCode)
  const currentStrategyVersion = useBotStore((s) => s.currentStrategyVersion)
  const bumpVersionHistoryNonce = useBotStore((s) => s.bumpVersionHistoryNonce)

  const controls = useMemo(
    () => [
      { key: 'days', label: '天数', inputMode: 'numeric' },
      { key: 'equity', label: '初始资金($)', inputMode: 'numeric' },
      { key: 'leverage', label: '杠杆', inputMode: 'numeric' },
      { key: 'fee_rate', label: '手续费%/单', inputMode: 'decimal' },
    ],
    [],
  )

  useEffect(() => {
    if (!timeframe || form.timeframe === timeframe) return
    setForm((current) => ({ ...current, timeframe }))
  }, [form.timeframe, timeframe])

  useEffect(() => {
    if (!form.timeframe || form.timeframe === timeframe) return
    onTimeframeChange?.(form.timeframe)
  }, [form.timeframe, onTimeframeChange, timeframe])

  function handleSaveDefaults() {
    if (typeof window === 'undefined') return

    window.localStorage.setItem(BACKTEST_DEFAULTS_KEY, JSON.stringify(parseBacktestForm(form)))
    setSavedNotice('默认回测参数已保存')
    window.setTimeout(() => {
      setSavedNotice((current) => (current === '默认回测参数已保存' ? null : current))
    }, 2500)
  }

  async function runBacktest() {
    setStatus('running')
    setResult(null)
    setError(null)
    setSavedNotice(null)
    setBacktestFeedback(null)
    setBacktestFeedbackStatus(null)
    const parsedForm = parseBacktestForm(form)
    if (!strategyCode?.trim()) {
      setError('暂无可回测的策略代码，请先在右侧生成或加载策略代码')
      setStatus('error')
      return
    }
    try {
      const payload = {
        strategy_code: strategyCode,
        strategy_version_id: currentStrategyVersion?.id ?? null,
        symbol: 'BTC/USDT:USDT',
        timeframe: parsedForm.timeframe,
        days: parsedForm.days,
        initial_equity: parsedForm.equity,
        leverage: parsedForm.leverage,
        fee_rate: parsedForm.fee_rate / 100,   // UI shows 0.05%, API expects 0.0005
      }
      const res = await fetch('/api/strategy/backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '回测启动失败')
      const { job_id } = data
      poll(job_id, parsedForm.leverage)
    } catch (e) {
      setError(String(e))
      setStatus('error')
    }
  }

  async function poll(jid, leverage) {
    try {
      const r = await fetch(`/api/strategy/backtest/${jid}`)
      const data = await r.json()
      if (data.status === 'running') {
        setTimeout(() => poll(jid, leverage), 2000)
    } else if (data.status === 'done') {
      setResult(data)
      setStatus('done')
      // Push trades + summary (with leverage) into global store so TradesTable can display them
      setBacktestResult(data.trades || [], { ...data.summary, _leverage: leverage, _job_id: data.job_id })
      if (currentStrategyVersion?.id) {
        bumpVersionHistoryNonce()
      }
      } else {
        setError(data.error || 'Unknown error')
        setStatus('error')
      }
    } catch (e) {
      setError(String(e))
      setStatus('error')
    }
  }

  const s = result?.summary
  const summaryCards = s
    ? [
        { label: '总交易', value: s.total_trades },
        {
          label: '胜率',
          value: `${s.win_rate_pct?.toFixed(1)}%`,
          color: s.win_rate_pct >= 50 ? 'text-green-400' : 'text-yellow-400',
        },
        {
          label: `总收益 (含手续费)`,
          value: `$${s.total_pnl_usdt?.toFixed(2)}`,
          color: s.total_pnl_usdt >= 0 ? 'text-green-400' : 'text-red-400',
        },
        {
          label: '最大回撤',
          value: `${s.max_drawdown_pct?.toFixed(2)}%`,
          color: 'text-red-400',
        },
        {
          label: `收益率 (${s.leverage ?? form.leverage}x杠杆)`,
          value: `${s.total_return_pct?.toFixed(2)}%`,
          color: s.total_return_pct >= 0 ? 'text-green-400' : 'text-red-400',
        },
        {
          label: '盈亏比',
          value: s.profit_factor?.toFixed(2) || '—',
        },
        {
          label: `手续费合计 (${s.fee_rate_pct ?? form.fee_rate}%/单)`,
          value: `$${s.total_fee_usdt?.toFixed(2)}`,
          color: 'text-orange-400',
        },
        {
          label: '期间',
          value: `${dayjs(s.date_from).format('MM/DD')} — ${dayjs(s.date_to).format('MM/DD')}`,
        },
        { label: '最终权益', value: `$${s.final_equity?.toFixed(2)}` },
      ]
    : []

  return (
    <div className="bg-gray-900 rounded-lg p-3">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-gray-300">运行策略</h2>
          <p className="mt-1 text-xs text-gray-500">回测参数会沿用你保存的默认值。</p>
        </div>
        <button
          type="button"
          onClick={handleSaveDefaults}
          className="rounded-md border border-gray-700 px-3 py-1.5 text-xs font-medium text-gray-300 transition-colors hover:border-gray-500 hover:bg-gray-800 hover:text-gray-100"
        >
          设为默认
        </button>
      </div>

      <div className="flex flex-wrap gap-3 items-end mb-3">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-gray-500">时间周期</label>
          <select
            value={form.timeframe}
            onChange={(e) => {
              const nextTimeframe = e.target.value
              setForm((current) => ({ ...current, timeframe: nextTimeframe }))
              onTimeframeChange?.(nextTimeframe)
            }}
            className="w-24 bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
          >
            {TIMEFRAME_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>
        {controls.map(({ key, label, ...rest }) => (
          <div key={key} className="flex flex-col gap-1">
            <label className="text-xs text-gray-500">{label}</label>
            <input
              type="text"
              value={form[key]}
              onChange={(e) =>
                setForm((f) => ({ ...f, [key]: sanitizeBacktestInput(key, e.target.value) }))
              }
              autoComplete="off"
              spellCheck="false"
              {...rest}
              className="w-24 bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
            />
          </div>
        ))}
        <button
          onClick={runBacktest}
          disabled={status === 'running'}
          className="px-5 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded text-sm font-medium text-white transition-colors"
        >
          {status === 'running' ? '运行中...' : '运行回测'}
        </button>
      </div>

      {savedNotice && <div className="mb-3 text-xs text-green-400">{savedNotice}</div>}

      {status === 'running' && (
        <div className="text-blue-400 text-sm mb-3 flex items-center gap-2">
          <span className="w-3 h-3 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
          正在获取数据并计算，请稍候...
        </div>
      )}

      {error && <div className="text-red-400 text-sm mb-3">错误: {error}</div>}

      {result?.summary && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
            {summaryCards.map(({ label, value, color = 'text-white' }) => (
              <div key={label} className="bg-gray-800 rounded p-3 text-center">
                <div className={`text-lg font-bold font-mono ${color}`}>{value}</div>
                <div className="text-xs text-gray-500 mt-0.5">{label}</div>
              </div>
            ))}
          </div>

          {result.equity_curve?.length > 0 && (
            <div>
              <ResponsiveContainer width="100%" height={156}>
                <LineChart data={result.equity_curve}>
                  <XAxis
                    dataKey="ts"
                    tickFormatter={(d) => dayjs(d).format('MM/DD')}
                    stroke="#4b5563"
                    tick={{ fontSize: 10 }}
                  />
                  <YAxis
                    stroke="#4b5563"
                    tick={{ fontSize: 10 }}
                    tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`}
                    domain={['auto', 'auto']}
                  />
                  <Tooltip
                    contentStyle={{
                      background: '#1f2937',
                      border: '1px solid #374151',
                      borderRadius: 6,
                    }}
                    formatter={(v) => [`$${v.toFixed(2)}`, '权益']}
                    labelFormatter={(d) => dayjs(d).format('YYYY-MM-DD')}
                  />
                  <Line
                    type="monotone"
                    dataKey="equity"
                    stroke="#3b82f6"
                    dot={false}
                    strokeWidth={2}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      )}

    </div>
  )
}
