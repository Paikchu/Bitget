import { useState } from 'react'
import { useTrades, useStatus } from '../hooks/useApi'
import useBotStore from '../store/botStore'
import dayjs from 'dayjs'

function normalizeTradeFeedback(feedback) {
  if (!feedback || typeof feedback !== 'object') return null
  const overallDiagnosis = (() => {
    if (typeof feedback.overall_diagnosis === 'string') {
      return { headline: '总诊断', summary: feedback.overall_diagnosis }
    }
    return {
      headline: feedback.overall_diagnosis?.headline || '未提供',
      summary: feedback.overall_diagnosis?.summary || '',
    }
  })()

  const evidence = Array.isArray(feedback.evidence)
    ? feedback.evidence
    : typeof feedback.evidence === 'string' && feedback.evidence
      ? [feedback.evidence]
      : []

  return {
    overall_score: feedback.overall_score ?? '—',
    overall_diagnosis: overallDiagnosis,
    issue_groups: Array.isArray(feedback.issue_groups)
      ? feedback.issue_groups.map((item, index) => ({
          key: `${item.title || item.issue_name || 'issue'}-${index}`,
          title: item.title || item.issue_name || '问题分组',
          pattern: item.pattern || item.description || '',
          impact: item.impact || '',
          why_it_happens: item.why_it_happens || item.evidence || '',
          trade_numbers: Array.isArray(item.trade_numbers) ? item.trade_numbers : [],
          recommendations: Array.isArray(item.recommendations)
            ? item.recommendations
            : item.suggested_fix
              ? [item.suggested_fix]
              : [],
        }))
      : [],
    priority_actions: Array.isArray(feedback.priority_actions) ? feedback.priority_actions : [],
    evidence,
    confidence: feedback.confidence ?? '—',
  }
}

// pnl_pct is stored as a percentage value already (e.g., -0.71 means -0.71% price move).
// pnl_usdt reflects the leveraged notional, so pnl_usdt / equity_at_entry = margin return.
function TradeRow({ t, leverage = 1 }) {
  const isOpen = !t.exit_time
  const pnlPos = (t.pnl_usdt || 0) >= 0
  // Margin return % = price_change% × leverage (return on capital deployed)
  const marginRetPct = t.pnl_pct != null ? t.pnl_pct * leverage : null
  return (
    <tr
      className={`border-b border-gray-800 hover:bg-gray-800/50 ${isOpen ? 'bg-blue-900/10' : ''}`}
    >
      <td className="py-2 px-2 text-gray-500">{t.id ?? '—'}</td>
      <td className="py-2 px-2">
        <span
          className={`px-1.5 py-0.5 rounded text-xs font-bold ${
            t.direction === 'long'
              ? 'bg-green-500/20 text-green-400'
              : 'bg-red-500/20 text-red-400'
          }`}
        >
          {t.direction === 'long' ? '多' : '空'}
        </span>
      </td>
      <td className="py-2 px-2 text-gray-400 font-mono">
        {t.entry_time ? dayjs(t.entry_time).format('MM-DD HH:mm') : '—'}
      </td>
      <td className="py-2 px-2 text-right font-mono">
        {t.entry_price != null ? Number(t.entry_price).toFixed(1) : '—'}
      </td>
      <td className="py-2 px-2 text-gray-400 font-mono">
        {isOpen ? (
          <span className="text-blue-400 animate-pulse">持仓中</span>
        ) : t.exit_time ? (
          dayjs(t.exit_time).format('MM-DD HH:mm')
        ) : (
          '—'
        )}
      </td>
      <td className="py-2 px-2 text-right font-mono">
        {t.exit_price != null ? Number(t.exit_price).toFixed(1) : '—'}
      </td>
      <td
        className={`py-2 px-2 text-right font-mono ${pnlPos ? 'text-green-400' : 'text-red-400'}`}
      >
        {marginRetPct != null ? `${marginRetPct >= 0 ? '+' : ''}${marginRetPct.toFixed(2)}%` : '—'}
      </td>
      <td
        className={`py-2 px-2 text-right font-mono font-bold ${pnlPos ? 'text-green-400' : 'text-red-400'}`}
      >
        {t.pnl_usdt != null ? `$${Number(t.pnl_usdt).toFixed(2)}` : '—'}
      </td>
    </tr>
  )
}

function LiveTable() {
  const [page, setPage] = useState(1)
  const [direction, setDirection] = useState(null)
  const { data: tradesData } = useTrades(page, direction)
  const { data: statusData } = useStatus()
  const trades = tradesData?.trades || []
  const total = tradesData?.total || 0
  const totalPages = Math.ceil(total / 50)
  const leverage = statusData?.leverage || 1

  return (
    <>
      <div className="flex gap-2 mb-3">
        {[null, 'long', 'short'].map((d) => (
          <button
            key={d || 'all'}
            onClick={() => { setDirection(d); setPage(1) }}
            className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
              direction === d ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-white'
            }`}
          >
            {d === null ? '全部' : d === 'long' ? '多' : '空'}
          </button>
        ))}
      </div>
      <TableBody trades={trades} leverage={leverage} empty="暂无实盘交易记录" />
      {totalPages > 1 && (
        <Pagination page={page} totalPages={totalPages} setPage={setPage} />
      )}
    </>
  )
}

function BacktestTable() {
  const [page, setPage] = useState(1)
  const [direction, setDirection] = useState(null)
  const [feedbackError, setFeedbackError] = useState(null)
  const rawTrades = useBotStore((s) => s.backtestTrades) || []
  const summary = useBotStore((s) => s.backtestSummary)
  const backtestFeedback = useBotStore((s) => s.backtestFeedback)
  const backtestFeedbackStatus = useBotStore((s) => s.backtestFeedbackStatus)
  const setBacktestFeedback = useBotStore((s) => s.setBacktestFeedback)
  const setBacktestFeedbackStatus = useBotStore((s) => s.setBacktestFeedbackStatus)
  // Derive leverage from summary: total_return_pct / (total_pnl_usdt/initial_equity*100)
  // Simpler: store it directly via the form. For now, infer from summary or default 5.
  const leverage = summary?._leverage ?? summary?.leverage ?? 5
  const normalizedFeedback = normalizeTradeFeedback(backtestFeedback?.feedback)

  const filtered = direction ? rawTrades.filter((t) => t.direction === direction) : rawTrades
  const pageSize = 50
  const totalPages = Math.ceil(filtered.length / pageSize)
  const trades = filtered.slice((page - 1) * pageSize, page * pageSize)

  async function runTradeFeedback() {
    if (!summary?._job_id) return
    setFeedbackError(null)
    setBacktestFeedbackStatus('running')
    try {
      const response = await fetch(`/api/strategy/backtest/${summary._job_id}/feedback`, {
        method: 'POST',
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || '交易分析失败')
      setBacktestFeedback(data)
      setBacktestFeedbackStatus('done')
    } catch (e) {
      setFeedbackError(String(e))
      setBacktestFeedbackStatus('error')
    }
  }

  return (
    <>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-400">回测成交记录</h2>
        <button
          type="button"
          onClick={runTradeFeedback}
          disabled={!summary?._job_id || rawTrades.length === 0 || backtestFeedbackStatus === 'running'}
          className="rounded border border-gray-700 px-3 py-1 text-xs text-gray-300 transition-colors hover:border-gray-500 hover:bg-gray-800 hover:text-white disabled:opacity-50"
        >
          {backtestFeedbackStatus === 'running' ? '分析中...' : 'AI分析交易'}
        </button>
      </div>
      <div className="flex items-center justify-between mb-3">
        <div className="flex gap-2">
          {[null, 'long', 'short'].map((d) => (
            <button
              key={d || 'all'}
              onClick={() => { setDirection(d); setPage(1) }}
              className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                direction === d ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-white'
              }`}
            >
              {d === null ? '全部' : d === 'long' ? '多' : '空'}
            </button>
          ))}
        </div>
        {summary && (
          <span className="text-xs text-gray-500">
            共 {rawTrades.length} 笔 ·{' '}
            <span className={summary.total_return_pct >= 0 ? 'text-green-400' : 'text-red-400'}>
              {summary.total_return_pct >= 0 ? '+' : ''}{summary.total_return_pct?.toFixed(2)}%
            </span>
            {' '}· {leverage}x
          </span>
        )}
      </div>
      <TableBody
        trades={trades.map((t, i) => ({ ...t, id: (page - 1) * pageSize + i + 1 }))}
        leverage={leverage}
        empty="暂无回测交易记录，请先运行回测"
        maxHeightClass="max-h-[60vh]"
      />
      {totalPages > 1 && (
        <Pagination page={page} totalPages={totalPages} setPage={setPage} />
      )}
      {feedbackError && <div className="mt-3 text-sm text-red-400">错误: {feedbackError}</div>}
      {normalizedFeedback && (
        <div className="mt-4 space-y-4 rounded-lg border border-gray-800 bg-black/20 p-4">
          <div className="grid gap-3 lg:grid-cols-[180px_1fr]">
            <div className="rounded bg-gray-800 p-4">
              <div className="text-xs text-gray-500">总诊断评分</div>
              <div className="mt-2 text-2xl font-bold font-mono text-white">{normalizedFeedback.overall_score}</div>
              <div className="mt-3 text-xs text-gray-500">置信度 {normalizedFeedback.confidence}</div>
            </div>
            <div className="rounded bg-gray-800 p-4">
              <div className="text-xs text-gray-500">总诊断</div>
              <div className="mt-2 text-sm font-semibold text-white">{normalizedFeedback.overall_diagnosis.headline}</div>
              <div className="mt-2 text-sm leading-6 text-gray-300">{normalizedFeedback.overall_diagnosis.summary}</div>
            </div>
          </div>

          <div className="rounded border border-gray-800 bg-gray-900 p-4">
            <div className="mb-3 text-xs text-gray-500">优先优化动作</div>
            <div className="space-y-2">
              {normalizedFeedback.priority_actions.map((item) => (
                <div key={item} className="rounded bg-gray-800 px-3 py-2 text-sm text-gray-200">{item}</div>
              ))}
            </div>
          </div>

          <div className="space-y-3">
            <div className="text-xs text-gray-500">问题交易分组</div>
            {normalizedFeedback.issue_groups.map((group) => (
              <div key={group.key} className="rounded border border-gray-800 bg-gray-900 p-4">
                <div className="text-sm font-semibold text-white">{group.title}</div>
                {group.pattern ? <div className="mt-2 text-sm text-gray-300">{group.pattern}</div> : null}
                {group.impact ? <div className="mt-2 text-xs text-red-300">影响: {group.impact}</div> : null}
                {group.why_it_happens ? (
                  <div className="mt-2 text-xs leading-6 text-gray-400">原因: {group.why_it_happens}</div>
                ) : null}
                {group.trade_numbers.length > 0 ? (
                  <div className="mt-3 text-xs text-gray-500">典型交易编号: {group.trade_numbers.join(', ')}</div>
                ) : null}
                {group.recommendations.length > 0 ? (
                  <div className="mt-3 space-y-2">
                    {group.recommendations.map((item) => (
                      <div key={item} className="rounded bg-gray-800 px-3 py-2 text-sm text-gray-200">{item}</div>
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
          </div>

          <div className="rounded border border-gray-800 bg-gray-900 p-4">
            <div className="mb-3 text-xs text-gray-500">证据</div>
            <div className="space-y-2">
              {normalizedFeedback.evidence.map((item) => (
                <div key={item} className="rounded bg-gray-800 px-3 py-2 text-sm text-gray-200">{item}</div>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  )
}

function TableBody({ trades, leverage = 1, empty, maxHeightClass = 'max-h-[56vh]' }) {
  return (
    <div className={`left-pane-scrollbar overflow-x-auto overflow-y-auto pr-1 ${maxHeightClass}`}>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-gray-500 border-b border-gray-800 sticky top-0 bg-gray-900 z-10">
            <th className="text-left py-2 px-2">#</th>
            <th className="text-left py-2 px-2">方向</th>
            <th className="text-left py-2 px-2">开仓时间</th>
            <th className="text-right py-2 px-2">开仓价</th>
            <th className="text-left py-2 px-2">平仓时间</th>
            <th className="text-right py-2 px-2">平仓价</th>
            <th className="text-right py-2 px-2 whitespace-nowrap">
              保证金收益%
              <span className="text-gray-600 ml-1">×{leverage}</span>
            </th>
            <th className="text-right py-2 px-2">PnL(USDT)</th>
          </tr>
        </thead>
        <tbody>
          {trades.length === 0 ? (
            <tr>
              <td colSpan={8} className="text-center py-8 text-gray-600">
                {empty}
              </td>
            </tr>
          ) : (
            trades.map((t, i) => <TradeRow key={t.id ?? i} t={t} leverage={leverage} />)
          )}
        </tbody>
      </table>
    </div>
  )
}

function Pagination({ page, totalPages, setPage }) {
  return (
    <div className="flex items-center justify-center gap-2 mt-4">
      <button
        onClick={() => setPage((p) => Math.max(1, p - 1))}
        disabled={page === 1}
        className="px-3 py-1 bg-gray-800 rounded disabled:opacity-40 text-sm hover:bg-gray-700"
      >
        ←
      </button>
      <span className="text-sm text-gray-400">
        {page} / {totalPages}
      </span>
      <button
        onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
        disabled={page === totalPages}
        className="px-3 py-1 bg-gray-800 rounded disabled:opacity-40 text-sm hover:bg-gray-700"
      >
        →
      </button>
    </div>
  )
}

/**
 * @param {'toggle' | 'live' | 'backtest'} [variant] — toggle: legacy tab switch; live/backtest: fixed list
 */
export default function TradesTable({ variant = 'toggle' }) {
  const backtestTrades = useBotStore((s) => s.backtestTrades)
  const hasBacktest = backtestTrades !== null
  const [mode, setMode] = useState('live')

  if (variant === 'live') {
    return (
      <div className="bg-gray-900 rounded-lg p-4">
        <h2 className="text-sm font-semibold text-gray-400 mb-3">实盘成交记录</h2>
        <LiveTable />
      </div>
    )
  }

  if (variant === 'backtest') {
    return (
      <div className="bg-gray-900 rounded-lg p-4 flex min-h-0 flex-col">
        <BacktestTable />
      </div>
    )
  }

  return (
    <div className="bg-gray-900 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-400">历史交易记录</h2>
        <div className="flex bg-gray-800 rounded p-0.5 gap-0.5">
          <button
            onClick={() => setMode('live')}
            className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
              mode === 'live' ? 'bg-gray-600 text-white' : 'text-gray-400 hover:text-white'
            }`}
          >
            实盘记录
          </button>
          <button
            onClick={() => setMode('backtest')}
            className={`px-3 py-1 rounded text-xs font-medium transition-colors relative ${
              mode === 'backtest' ? 'bg-gray-600 text-white' : 'text-gray-400 hover:text-white'
            }`}
          >
            回测记录
            {hasBacktest && mode !== 'backtest' && (
              <span className="absolute -top-1 -right-1 w-2 h-2 bg-blue-400 rounded-full" />
            )}
          </button>
        </div>
      </div>

      {mode === 'live' ? <LiveTable /> : <BacktestTable />}
    </div>
  )
}
