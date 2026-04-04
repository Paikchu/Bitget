function formatCreatedAt(value) {
  if (!value) return '未知时间'

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value

  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatPercent(value) {
  return typeof value === 'number' ? `${value >= 0 ? '+' : ''}${value.toFixed(2)}%` : '—'
}

function formatUsdt(value) {
  return typeof value === 'number' ? `${value >= 0 ? '+' : ''}$${value.toFixed(2)}` : '—'
}

export default function VersionHistoryPanel({
  open,
  versions,
  currentVersion,
  loading,
  restoring,
  error,
  onRefresh,
  onRestore,
  onClose,
}) {
  if (!open) return null

  return (
    <div className="absolute inset-0 z-30">
      <button
        type="button"
        aria-label="关闭历史版本抽屉"
        className="absolute inset-0 bg-black/45"
        onClick={onClose}
      />
      <aside className="absolute inset-y-0 right-0 flex w-full max-w-[460px] flex-col border-l border-gray-800 bg-gray-950 shadow-2xl">
        <div className="flex items-start justify-between gap-3 border-b border-gray-800 px-4 py-4">
          <div>
            <div className="text-sm font-semibold text-gray-100">历史版本</div>
            <div className="pt-1 text-xs text-gray-500">每次成功生成代码后自动保存</div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onRefresh}
              disabled={loading}
              className="rounded border border-gray-700 px-2.5 py-1 text-[11px] text-gray-400 transition-colors hover:border-gray-500 hover:text-gray-200 disabled:cursor-not-allowed disabled:opacity-50"
            >
              刷新
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded border border-gray-700 px-2.5 py-1 text-[11px] text-gray-400 transition-colors hover:border-gray-500 hover:text-gray-200"
            >
              关闭
            </button>
          </div>
        </div>

        {error && <div className="px-4 py-3 text-xs text-red-400">{error}</div>}

        <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
          {!versions.length && !loading ? (
            <div className="rounded-xl border border-dashed border-gray-800 px-4 py-5 text-sm text-gray-500">
              还没有历史版本。先生成一版新策略代码，系统会自动记录。
            </div>
          ) : null}

          <div className="space-y-3">
            {versions.map((version) => {
              const isActive = currentVersion?.id === version.id
              const summary = version.latest_backtest_summary

              return (
                <button
                  key={version.id}
                  type="button"
                  onClick={() => onRestore(version)}
                  disabled={restoring}
                  className={`w-full rounded-xl border px-4 py-3 text-left transition-colors ${
                    isActive
                      ? 'border-blue-700 bg-blue-950/30'
                      : 'border-gray-800 bg-gray-900 hover:border-gray-700 hover:bg-gray-900/80'
                  } disabled:cursor-not-allowed disabled:opacity-60`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 text-xs">
                        <span className="font-semibold text-gray-100">V{version.version_no}</span>
                        <span className="rounded bg-gray-800 px-1.5 py-0.5 text-[10px] text-gray-400">
                          {version.source}
                        </span>
                        {isActive ? (
                          <span className="rounded bg-blue-900/60 px-1.5 py-0.5 text-[10px] text-blue-300">
                            当前
                          </span>
                        ) : null}
                      </div>
                      <div className="truncate pt-1 text-sm font-medium text-gray-200">
                        {version.title || '未命名策略'}
                      </div>
                      <div className="pt-1 text-[11px] text-gray-500">
                        {formatCreatedAt(version.created_at)}
                      </div>
                    </div>
                    <div className="pt-0.5 text-[11px] text-gray-500">
                      {restoring ? '载入中...' : '点击载入'}
                    </div>
                  </div>

                  <div className="mt-3 rounded-lg border border-gray-800 bg-gray-950/80 px-3 py-2">
                    {summary ? (
                      <>
                        <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-[11px]">
                          <div>
                            <div className="text-gray-500">总收益率</div>
                            <div className={`pt-0.5 font-mono ${summary.total_return_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                              {formatPercent(summary.total_return_pct)}
                            </div>
                          </div>
                          <div>
                            <div className="text-gray-500">净利润</div>
                            <div className={`pt-0.5 font-mono ${summary.total_pnl_usdt >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                              {formatUsdt(summary.total_pnl_usdt)}
                            </div>
                          </div>
                          <div>
                            <div className="text-gray-500">总交易数</div>
                            <div className="pt-0.5 font-mono text-gray-200">{summary.total_trades ?? '—'}</div>
                          </div>
                          <div>
                            <div className="text-gray-500">胜率</div>
                            <div className="pt-0.5 font-mono text-gray-200">
                              {typeof summary.win_rate_pct === 'number' ? `${summary.win_rate_pct.toFixed(1)}%` : '—'}
                            </div>
                          </div>
                          <div>
                            <div className="text-gray-500">最大回撤</div>
                            <div className="pt-0.5 font-mono text-red-400">
                              {typeof summary.max_drawdown_pct === 'number' ? `${summary.max_drawdown_pct.toFixed(2)}%` : '—'}
                            </div>
                          </div>
                          <div>
                            <div className="text-gray-500">回测时间</div>
                            <div className="pt-0.5 font-mono text-gray-300">{formatCreatedAt(version.latest_backtest_at)}</div>
                          </div>
                        </div>
                      </>
                    ) : (
                      <div className="text-[11px] text-gray-500">暂无回测记录</div>
                    )}
                  </div>
                </button>
              )
            })}
          </div>
        </div>
      </aside>
    </div>
  )
}
