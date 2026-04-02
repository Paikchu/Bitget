import useBotStore from '../store/botStore'
import { useStatus } from '../hooks/useApi'

export default function StatusBar() {
  const { data } = useStatus()
  const wsConnected = useBotStore((s) => s.wsConnected)
  const status = data || {}

  return (
    <div className="flex items-center justify-between px-6 py-3 bg-gray-900 border-b border-gray-700 text-sm">
      <div className="flex items-center gap-6">
        <span className="text-white font-bold text-base">{status.symbol || 'BTC/USDT'}</span>
        <span className="text-gray-400">
          资金:{' '}
          <span className="text-white font-mono">
            ${(status.current_equity || 0).toLocaleString('en-US', { minimumFractionDigits: 2 })}
          </span>
        </span>
        <span
          className={`font-mono ${(status.return_pct || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}
        >
          {(status.return_pct || 0) >= 0 ? '▲' : '▼'} {Math.abs(status.return_pct || 0).toFixed(2)}%
        </span>
      </div>

      <div className="flex items-center gap-4">
        {status.dry_run && (
          <span className="px-2 py-0.5 bg-yellow-500/20 text-yellow-400 rounded text-xs">
            模拟交易
          </span>
        )}
        <span className="flex items-center gap-1.5">
          <span
            className={`w-2 h-2 rounded-full ${status.running ? 'bg-green-400 animate-pulse' : 'bg-red-400'}`}
          />
          <span className="text-gray-300">{status.running ? '运行中' : '已停止'}</span>
        </span>
        {status.current_position && (
          <span
            className={`px-2 py-0.5 rounded text-xs font-bold ${
              status.current_position === 'long'
                ? 'bg-green-500/20 text-green-400'
                : 'bg-red-500/20 text-red-400'
            }`}
          >
            {status.current_position === 'long' ? '多仓' : '空仓'}
          </span>
        )}
        <span
          className={`flex items-center gap-1 text-xs ${wsConnected ? 'text-green-400' : 'text-red-400'}`}
        >
          <span
            className={`w-1.5 h-1.5 rounded-full ${wsConnected ? 'bg-green-400' : 'bg-red-400'}`}
          />
          {wsConnected ? 'WS 已连接' : 'WS 断开'}
        </span>
      </div>
    </div>
  )
}
