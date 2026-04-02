import { Component, useState } from 'react'
import { useWebSocket } from './hooks/useWebSocket'
import StatusBar from './components/StatusBar'
import CandleChart from './components/CandleChart'
import EquityChart from './components/EquityChart'
import StatsPanel from './components/StatsPanel'
import TradesTable from './components/TradesTable'
import BacktestPanel from './components/BacktestPanel'
import StrategyStudio from './components/StrategyStudio'

class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }
  static getDerivedStateFromError(error) { return { error } }
  render() {
    if (this.state.error) {
      return (
        <div className="bg-red-900/20 border border-red-800 rounded-lg p-4 text-red-400 text-sm">
          <div className="font-bold mb-1">组件错误</div>
          <div className="font-mono text-xs">{String(this.state.error)}</div>
        </div>
      )
    }
    return this.props.children
  }
}

const TABS = [
  { id: 'dashboard', label: '📊 监控面板' },
  { id: 'studio',    label: '✦ 策略工作台' },
]

function App() {
  const [activeTab, setActiveTab] = useState('dashboard')
  useWebSocket()

  return (
    <div className="min-h-screen bg-gray-950 text-white">
      <div className="border-b border-gray-800 bg-gray-950 sticky top-0 z-50">
        <div className="max-w-screen-2xl mx-auto flex items-center gap-0 px-4">
          <span className="text-gray-400 text-sm font-mono mr-6 py-3">Bitget Bot</span>
          {TABS.map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-5 py-3 text-sm font-medium border-b-2 transition-colors ${
                activeTab === tab.id
                  ? 'border-blue-500 text-blue-400'
                  : 'border-transparent text-gray-400 hover:text-gray-200'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {activeTab === 'dashboard' && (
        <>
          <ErrorBoundary><StatusBar /></ErrorBoundary>
          <div className="max-w-screen-2xl mx-auto p-4 space-y-4">
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
              <div className="lg:col-span-1"><ErrorBoundary><EquityChart /></ErrorBoundary></div>
              <div className="lg:col-span-2"><ErrorBoundary><CandleChart /></ErrorBoundary></div>
            </div>
            <ErrorBoundary><StatsPanel /></ErrorBoundary>
            <ErrorBoundary><TradesTable /></ErrorBoundary>
            <ErrorBoundary><BacktestPanel /></ErrorBoundary>
          </div>
        </>
      )}

      {activeTab === 'studio' && (
        <ErrorBoundary><StrategyStudio /></ErrorBoundary>
      )}
    </div>
  )
}

export default App
