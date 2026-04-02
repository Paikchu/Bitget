import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
} from 'recharts'
import { useEquity } from '../hooks/useApi'
import useBotStore from '../store/botStore'
import dayjs from 'dayjs'

export default function EquityChart() {
  const { data } = useEquity()
  const livePoints = useBotStore((s) => s.equityPoints)

  const apiPoints = data?.points || []
  const points = apiPoints.length > 0 ? apiPoints : livePoints
  const initial = data?.initial_equity || 10000
  const current = data?.current_equity || initial
  const returnPct = data?.return_pct || 0

  return (
    <div className="bg-gray-900 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-400">权益曲线</h2>
        <div className="text-right">
          <div className="text-white font-mono font-bold">
            ${current.toLocaleString('en-US', { minimumFractionDigits: 2 })}
          </div>
          <div
            className={`text-xs font-mono ${returnPct >= 0 ? 'text-green-400' : 'text-red-400'}`}
          >
            {returnPct >= 0 ? '+' : ''}
            {returnPct.toFixed(2)}%
          </div>
        </div>
      </div>

      {points.length === 0 ? (
        <div className="flex items-center justify-center h-40 text-gray-600 text-sm">暂无数据</div>
      ) : (
        <ResponsiveContainer width="100%" height={160}>
          <LineChart data={points} margin={{ top: 5, right: 5, left: 5, bottom: 5 }}>
            <XAxis
              dataKey="ts"
              tickFormatter={(d) => dayjs(d).format('MM/DD')}
              stroke="#4b5563"
              tick={{ fontSize: 11 }}
            />
            <YAxis
              domain={['auto', 'auto']}
              stroke="#4b5563"
              tick={{ fontSize: 11 }}
              tickFormatter={(v) => `$${(v / 1000).toFixed(1)}k`}
            />
            <Tooltip
              contentStyle={{
                background: '#1f2937',
                border: '1px solid #374151',
                borderRadius: 6,
              }}
              labelFormatter={(d) => dayjs(d).format('YYYY-MM-DD HH:mm')}
              formatter={(v) => [`$${v.toFixed(2)}`, '权益']}
            />
            <ReferenceLine y={initial} stroke="#6b7280" strokeDasharray="4 4" />
            <Line type="monotone" dataKey="equity" stroke="#22c55e" dot={false} strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}
