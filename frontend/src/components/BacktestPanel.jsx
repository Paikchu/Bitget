import { useState } from 'react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import dayjs from 'dayjs'
import useBotStore from '../store/botStore'

export default function BacktestPanel() {
  const [form, setForm] = useState({ days: 90, squeeze: 0.35, equity: 10000, leverage: 5, fee_rate: 0.05 })
  const [status, setStatus] = useState(null)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const setBacktestResult = useBotStore((s) => s.setBacktestResult)

  async function runBacktest() {
    setStatus('running')
    setResult(null)
    setError(null)
    try {
      const payload = {
        symbol: 'BTC/USDT:USDT',
        days: form.days,
        squeeze: form.squeeze,
        equity: form.equity,
        leverage: form.leverage,
        fee_rate: form.fee_rate / 100,   // UI shows 0.05%, API expects 0.0005
      }
      const res = await fetch('/api/backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const { job_id } = await res.json()
      poll(job_id)
    } catch (e) {
      setError(String(e))
      setStatus('error')
    }
  }

  async function poll(jid) {
    try {
      const r = await fetch(`/api/backtest/${jid}`)
      const data = await r.json()
      if (data.status === 'running') {
        setTimeout(() => poll(jid), 2000)
    } else if (data.status === 'done') {
      setResult(data)
      setStatus('done')
      // Push trades + summary (with leverage) into global store so TradesTable can display them
      setBacktestResult(data.trades || [], { ...data.summary, _leverage: form.leverage })
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
    <div className="bg-gray-900 rounded-lg p-4">
      <h2 className="text-sm font-semibold text-gray-400 mb-4">回测面板</h2>

      <div className="flex flex-wrap gap-4 items-end mb-4">
        {[
          { key: 'days', label: '天数', min: 7, max: 365, step: 1 },
          { key: 'squeeze', label: 'Squeeze阈值', step: '0.01', min: 0.1, max: 1 },
          { key: 'equity', label: '初始资金($)', min: 10, step: 1 },
          { key: 'leverage', label: '杠杆', min: 1, max: 125, step: 1 },
          { key: 'fee_rate', label: '手续费%/单', min: 0, max: 1, step: '0.01' },
        ].map(({ key, label, ...rest }) => (
          <div key={key} className="flex flex-col gap-1">
            <label className="text-xs text-gray-500">{label}</label>
            <input
              type="number"
              {...rest}
              value={form[key]}
              onChange={(e) => setForm((f) => ({ ...f, [key]: Number(e.target.value) }))}
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

      {status === 'running' && (
        <div className="text-blue-400 text-sm mb-3 flex items-center gap-2">
          <span className="w-3 h-3 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
          正在获取数据并计算，请稍候...
        </div>
      )}

      {error && <div className="text-red-400 text-sm mb-3">错误: {error}</div>}

      {result?.summary && (
        <div className="space-y-4">
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
              <h3 className="text-xs text-gray-500 mb-2">权益曲线</h3>
              <ResponsiveContainer width="100%" height={180}>
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
