import { useState } from 'react'
import StatusBar from './StatusBar'
import CandleChart from './CandleChart'
import EquityChart from './EquityChart'
import StatsPanel from './StatsPanel'
import TradesTable from './TradesTable'
import BacktestPanel from './BacktestPanel'

function PanelShell({ subtitle, children }) {
  return (
    <section className="rounded-xl border border-gray-800 bg-gray-950/60 overflow-hidden">
      {subtitle ? (
        <header className="px-4 py-3 border-b border-gray-800 bg-gray-900/90">
          <p className="text-xs text-gray-500">{subtitle}</p>
        </header>
      ) : null}
      <div className="p-4 space-y-4">{children}</div>
    </section>
  )
}

/**
 * Left-column dashboard: two standalone panels — live trading vs backtesting —
 * each with its own K-line chart and context-appropriate widgets.
 */
export default function DashboardPanels() {
  const [activePage, setActivePage] = useState('live')

  return (
    <div className="max-w-screen-2xl mx-auto h-full w-full min-w-0 flex min-h-0 flex-col overflow-hidden px-4 pb-6 pt-0 box-border">
      <div className="sticky top-0 z-10 -mx-4 mb-2 border-b border-gray-800 bg-gray-950 px-4 pb-2 pt-0">
        <div className="flex items-center justify-between rounded-lg border border-gray-800 bg-gray-900 px-2 py-1.5">
          <h1 className="text-sm text-gray-400 font-semibold px-2">监控看板</h1>
          <div
            className="flex bg-gray-800 rounded p-0.5 gap-0.5"
            role="tablist"
            aria-label="监控页面切换"
          >
            <button
              type="button"
              role="tab"
              aria-selected={activePage === 'live'}
              id="dash-tab-live"
              onClick={() => setActivePage('live')}
              className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                activePage === 'live' ? 'bg-gray-600 text-white' : 'text-gray-400 hover:text-white'
              }`}
            >
              实盘交易
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activePage === 'backtest'}
              id="dash-tab-backtest"
              onClick={() => setActivePage('backtest')}
              className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                activePage === 'backtest' ? 'bg-gray-600 text-white' : 'text-gray-400 hover:text-white'
              }`}
            >
              策略回测
            </button>
          </div>
        </div>
      </div>

      <div
        className="left-pane-scrollbar min-h-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-y-contain pr-1"
        role="tabpanel"
        aria-labelledby={activePage === 'live' ? 'dash-tab-live' : 'dash-tab-backtest'}
      >
        {activePage === 'live' ? (
          <PanelShell>
            <StatusBar />
            <CandleChart markerSource="live" />
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <EquityChart />
              <StatsPanel />
            </div>
            <TradesTable variant="live" />
          </PanelShell>
        ) : (
          <PanelShell>
            <CandleChart markerSource="backtest" />
            <BacktestPanel />
            <TradesTable variant="backtest" />
          </PanelShell>
        )}
      </div>
    </div>
  )
}
