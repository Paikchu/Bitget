import { useState } from 'react'
import { useTrades, useStatus } from '../hooks/useApi'
import useBotStore from '../store/botStore'
import dayjs from 'dayjs'

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
  const rawTrades = useBotStore((s) => s.backtestTrades) || []
  const summary = useBotStore((s) => s.backtestSummary)
  // Derive leverage from summary: total_return_pct / (total_pnl_usdt/initial_equity*100)
  // Simpler: store it directly via the form. For now, infer from summary or default 5.
  const leverage = summary?._leverage ?? summary?.leverage ?? 5

  const filtered = direction ? rawTrades.filter((t) => t.direction === direction) : rawTrades
  const pageSize = 50
  const totalPages = Math.ceil(filtered.length / pageSize)
  const trades = filtered.slice((page - 1) * pageSize, page * pageSize)

  return (
    <>
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
      />
      {totalPages > 1 && (
        <Pagination page={page} totalPages={totalPages} setPage={setPage} />
      )}
    </>
  )
}

function TableBody({ trades, leverage = 1, empty }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-gray-500 border-b border-gray-800">
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

export default function TradesTable() {
  const backtestTrades = useBotStore((s) => s.backtestTrades)
  const hasBacktest = backtestTrades !== null
  const [mode, setMode] = useState('live')

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
