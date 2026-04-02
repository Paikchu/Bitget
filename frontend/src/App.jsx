import { Component } from 'react'
import { useWebSocket } from './hooks/useWebSocket'
import StatusBar from './components/StatusBar'
import CandleChart from './components/CandleChart'
import EquityChart from './components/EquityChart'
import StatsPanel from './components/StatsPanel'
import TradesTable from './components/TradesTable'
import BacktestPanel from './components/BacktestPanel'

class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }
  static getDerivedStateFromError(error) {
    return { error }
  }
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

function Dashboard() {
  useWebSocket()

  return (
    <div className="min-h-screen bg-gray-950 text-white">
      <ErrorBoundary>
        <StatusBar />
      </ErrorBoundary>
      <div className="max-w-screen-2xl mx-auto p-4 space-y-4">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="lg:col-span-1">
            <ErrorBoundary>
              <EquityChart />
            </ErrorBoundary>
          </div>
          <div className="lg:col-span-2">
            <ErrorBoundary>
              <CandleChart />
            </ErrorBoundary>
          </div>
        </div>
        <ErrorBoundary>
          <StatsPanel />
        </ErrorBoundary>
        <ErrorBoundary>
          <TradesTable />
        </ErrorBoundary>
        <ErrorBoundary>
          <BacktestPanel />
        </ErrorBoundary>
      </div>
    </div>
  )
}

export default Dashboard
