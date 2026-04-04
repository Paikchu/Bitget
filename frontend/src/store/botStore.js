import { create } from 'zustand'

const useBotStore = create((set) => ({
  status: { running: false, dry_run: true, symbol: '', position: null, equity: 0 },
  setStatus: (data) => set((s) => ({ status: { ...s.status, ...data } })),

  trades: [],
  setTrades: (trades) => set({ trades }),
  updateTrade: (trade) =>
    set((s) => {
      const idx = s.trades.findIndex((t) => t.id === trade.id)
      if (idx >= 0) {
        const next = [...s.trades]
        next[idx] = trade
        return { trades: next }
      }
      return { trades: [trade, ...s.trades] }
    }),

  equityPoints: [],
  setEquityPoints: (pts) => set({ equityPoints: pts }),
  updateEquity: (pt) => set((s) => ({ equityPoints: [...s.equityPoints, pt] })),

  wsConnected: false,
  setWsConnected: (v) => set({ wsConnected: v }),

  // Last completed backtest result (trades list + summary)
  backtestTrades: null,   // null = no backtest run yet
  backtestSummary: null,
  setBacktestResult: (trades, summary) => set({ backtestTrades: trades, backtestSummary: summary }),

  strategyCode: '',
  setStrategyCode: (strategyCode) => set({ strategyCode }),

  strategyVersions: [],
  currentStrategyVersion: null,
  isVersionDrawerOpen: false,
  versionHistoryNonce: 0,
  setStrategyVersions: (strategyVersions) => set({ strategyVersions }),
  setCurrentStrategyVersion: (currentStrategyVersion) => set({ currentStrategyVersion }),
  openVersionDrawer: () => set({ isVersionDrawerOpen: true }),
  closeVersionDrawer: () => set({ isVersionDrawerOpen: false }),
  bumpVersionHistoryNonce: () => set((s) => ({ versionHistoryNonce: s.versionHistoryNonce + 1 })),
}))

export default useBotStore
