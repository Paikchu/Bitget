import { useEquity } from '../hooks/useApi'

function StatCard({ label, value, color = 'text-white' }) {
  return (
    <div className="bg-gray-800 rounded-lg px-3 py-4 text-center min-w-0">
      <div className={`text-lg sm:text-xl font-bold tabular-nums leading-none tracking-tight whitespace-nowrap ${color}`}>
        {value}
      </div>
      <div className="text-xs text-gray-500 mt-2 leading-tight whitespace-nowrap">
        {label}
      </div>
    </div>
  )
}

export default function StatsPanel() {
  const { data } = useEquity()
  const stats = data?.stats || {}

  const winRate = stats.win_rate_pct || 0
  const profitFactor =
    stats.avg_win_usdt && stats.avg_loss_usdt
      ? Math.abs(stats.avg_win_usdt / stats.avg_loss_usdt)
      : null

  return (
    <div className="bg-gray-900 rounded-lg p-4">
      <h2 className="text-sm font-semibold text-gray-400 mb-3">统计摘要</h2>
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <StatCard label="总交易" value={stats.total_trades || 0} />
        <StatCard label="做多" value={stats.long_trades || 0} color="text-green-400" />
        <StatCard label="做空" value={stats.short_trades || 0} color="text-red-400" />
        <StatCard
          label="胜率"
          value={`${winRate.toFixed(1)}%`}
          color={winRate >= 50 ? 'text-green-400' : 'text-yellow-400'}
        />
        <StatCard
          label="盈亏比"
          value={profitFactor ? profitFactor.toFixed(2) : '—'}
          color={profitFactor && profitFactor >= 1 ? 'text-green-400' : 'text-red-400'}
        />
        <StatCard
          label="总盈亏"
          value={`$${(stats.total_pnl_usdt || 0).toFixed(0)}`}
          color={(stats.total_pnl_usdt || 0) >= 0 ? 'text-green-400' : 'text-red-400'}
        />
      </div>
    </div>
  )
}
