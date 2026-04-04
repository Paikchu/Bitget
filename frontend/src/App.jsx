import { Component, useEffect, useState } from 'react'
import { useWebSocket } from './hooks/useWebSocket'
import DashboardPanels from './components/DashboardPanels'
import StrategyStudio from './components/StrategyStudio'
import ResizableSplitPane from './components/ResizableSplitPane'

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

const LG_MQ = '(min-width: 1024px)'

function useIsLg() {
  const [wide, setWide] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia(LG_MQ).matches,
  )
  useEffect(() => {
    const mq = window.matchMedia(LG_MQ)
    const onChange = () => setWide(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return wide
}

function App() {
  const isLg = useIsLg()
  useWebSocket()

  const dashboard = (
    <ErrorBoundary>
      <DashboardPanels />
    </ErrorBoundary>
  )

  const studio = (
    <ErrorBoundary>
      <StrategyStudio />
    </ErrorBoundary>
  )

  return (
    <div className="h-full min-h-0 bg-gray-950 text-white flex flex-col overflow-hidden">
      <header className="border-b border-gray-800 bg-gray-950 z-50 shrink-0 flex items-center px-4 h-12">
        <span className="text-gray-400 text-sm font-mono">Bitget Bot</span>
      </header>

      <div className="flex-1 min-h-0 flex flex-col">
        <ResizableSplitPane
          direction={isLg ? 'row' : 'col'}
          minPrimary={isLg ? 300 : 200}
          minSecondary={isLg ? 320 : 240}
          storageKey="bitget-dashboard-studio"
          primary={dashboard}
          secondary={studio}
        />
      </div>
    </div>
  )
}

export default App
