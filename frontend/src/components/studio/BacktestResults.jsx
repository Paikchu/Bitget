import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'
import dayjs from 'dayjs'

function StatCard({ label, value, sub, color = 'text-white' }) {
  return (
    <div className="bg-gray-900 rounded-lg p-3 border border-gray-800">
      <div className="text-xs text-gray-500 mb-1">{label}</div>
      <div className={`text-lg font-mono font-bold ${color}`}>{value}</div>
      {sub && <div className="text-xs text-gray-600 mt-0.5">{sub}</div>}
    </div>
  )
}

export default function BacktestResults({ result, loading, error }) {
  if (loading) {
    return (
      <div className="flex items-center justify-center h-48 text-gray-500 text-sm">
        <svg className="animate-spin h-5 w-5 mr-2" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
        </svg>
        在 Docker 沙箱中运行回测...
      </div>
    )
  }
  if (error) {
    return (
      <div className="bg-red-900/20 border border-red-800 rounded-lg p-4 m-4 text-red-400 text-sm">
        <div className="font-bold mb-1">回测失败</div>
        <pre className="font-mono text-xs whitespace-pre-wrap">{error}</pre>
      </div>
    )
  }
  if (!result) return null

  const { summary, equity_curve, trades } = result
  const retColor = summary.total_return_pct >= 0 ? 'text-green-400' : 'text-red-400'

  const chartData = equity_curve.map(p => ({
    ts: new Date(p.ts).getTime(),
    equity: p.equity,
  }))

  return (
    <div className="space-y-4 p-4">
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2">
        <StatCard label="总收益" value={`${summary.total_return_pct >= 0 ? '+' : ''}${summary.total_return_pct}%`} sub={`$${summary.total_pnl_usdt >= 0 ? '+' : ''}${summary.total_pnl_usdt}`} color={retColor} />
        <StatCard label="最终权益" value={`$${summary.final_equity?.toLocaleString()}`} sub={`初始 $${summary.initial_equity?.toLocaleString()}`} />
        <StatCard label="胜率" value={`${summary.win_rate_pct}%`} sub={`${summary.wins}胜 / ${summary.losses}负`} color={summary.win_rate_pct >= 50 ? 'text-green-400' : 'text-red-400'} />
        <StatCard label="盈亏比" value={summary.profit_factor ?? '∞'} color={(summary.profit_factor ?? 0) >= 1.5 ? 'text-green-400' : 'text-yellow-400'} />
        <StatCard label="最大回撤" value={`${summary.max_drawdown_pct}%`} color="text-red-400" />
        <StatCard label="总交易" value={summary.total_trades} sub={`多 ${summary.long_trades} / 空 ${summary.short_trades}`} />
        <StatCard label="手续费" value={`$${summary.total_fee_usdt}`} color="text-yellow-600" />
        <StatCard label="回测区间" value={dayjs(summary.date_from).format('MM/DD')} sub={`→ ${dayjs(summary.date_to).format('MM/DD')}`} />
      </div>

      <div className="bg-gray-900 rounded-lg p-4 border border-gray-800">
        <div className="text-xs text-gray-500 mb-3">权益曲线</div>
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={chartData}>
            <XAxis dataKey="ts" type="number" domain={['dataMin','dataMax']} tickFormatter={ts => dayjs(ts).format('MM/DD')} tick={{fontSize:11,fill:'#6b7280'}} axisLine={false} tickLine={false} />
            <YAxis domain={['auto','auto']} tick={{fontSize:11,fill:'#6b7280'}} axisLine={false} tickLine={false} tickFormatter={v=>`$${(v/1000).toFixed(1)}k`} width={55} />
            <Tooltip formatter={v=>[`$${Number(v).toFixed(2)}`,'权益']} labelFormatter={ts=>dayjs(ts).format('YYYY-MM-DD HH:mm')} contentStyle={{background:'#111827',border:'1px solid #374151',fontSize:12}} />
            <ReferenceLine y={summary.initial_equity} stroke="#4b5563" strokeDasharray="4 4" />
            <Line type="monotone" dataKey="equity" stroke={summary.total_return_pct >= 0 ? '#22c55e' : '#ef4444'} dot={false} strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="bg-gray-900 rounded-lg border border-gray-800 overflow-hidden">
        <div className="px-4 py-2 border-b border-gray-800 text-xs text-gray-500">交易记录（最近 50 条）</div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="text-gray-600 border-b border-gray-800">
                <th className="px-3 py-2 text-left">#</th>
                <th className="px-3 py-2 text-left">方向</th>
                <th className="px-3 py-2 text-left">开仓时间</th>
                <th className="px-3 py-2 text-right">开仓价</th>
                <th className="px-3 py-2 text-right">平仓价</th>
                <th className="px-3 py-2 text-right">PnL%</th>
                <th className="px-3 py-2 text-right">PnL(USDT)</th>
              </tr>
            </thead>
            <tbody>
              {(trades || []).slice(-50).map((t, idx) => {
                const isWin = t.pnl_usdt > 0
                const offset = Math.max(0, (trades||[]).length - 50)
                return (
                  <tr key={idx} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="px-3 py-1.5 text-gray-600">{offset + idx + 1}</td>
                    <td className="px-3 py-1.5"><span className={t.direction==='long'?'text-green-400':'text-red-400'}>{t.direction==='long'?'多':'空'}</span></td>
                    <td className="px-3 py-1.5 text-gray-400">{dayjs(t.entry_time).format('MM-DD HH:mm')}</td>
                    <td className="px-3 py-1.5 text-right text-gray-300">{t.entry_price?.toFixed(2)}</td>
                    <td className="px-3 py-1.5 text-right text-gray-300">{t.exit_price?.toFixed(2)}</td>
                    <td className={`px-3 py-1.5 text-right ${isWin?'text-green-400':'text-red-400'}`}>{t.pnl_pct>=0?'+':''}{t.pnl_pct?.toFixed(2)}%</td>
                    <td className={`px-3 py-1.5 text-right font-bold ${isWin?'text-green-400':'text-red-400'}`}>{t.pnl_usdt>=0?'+':''}${t.pnl_usdt?.toFixed(2)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
