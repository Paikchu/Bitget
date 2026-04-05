import { Component, useEffect, useState } from 'react'
import SettingsDrawer from './components/SettingsDrawer'
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
  const [settingsOpen, setSettingsOpen] = useState(false)
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

  const settingsIcon = (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className="h-6 w-6"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 3.75a2.25 2.25 0 0 1 2.19 1.72l.16.66a1.2 1.2 0 0 0 1.6.82l.63-.25a2.25 2.25 0 0 1 2.8.96 2.25 2.25 0 0 1-.31 2.95l-.47.49a1.2 1.2 0 0 0 0 1.7l.47.49a2.25 2.25 0 0 1 .31 2.95 2.25 2.25 0 0 1-2.8.96l-.63-.25a1.2 1.2 0 0 0-1.6.82l-.16.66a2.25 2.25 0 0 1-4.38 0l-.16-.66a1.2 1.2 0 0 0-1.6-.82l-.63.25a2.25 2.25 0 0 1-2.8-.96 2.25 2.25 0 0 1 .31-2.95l.47-.49a1.2 1.2 0 0 0 0-1.7l-.47-.49a2.25 2.25 0 0 1-.31-2.95 2.25 2.25 0 0 1 2.8-.96l.63.25a1.2 1.2 0 0 0 1.6-.82l.16-.66A2.25 2.25 0 0 1 12 3.75Z" />
      <circle cx="12" cy="12" r="3.1" />
    </svg>
  )

  return (
    <div className="h-full min-h-0 bg-gray-950 text-white flex overflow-hidden">
      <aside className="w-16 shrink-0 border-r border-gray-800 bg-[linear-gradient(180deg,rgba(5,10,24,0.98)_0%,rgba(3,7,18,0.98)_100%)] px-1.5 pb-5 pt-3">
        <div className="flex h-full flex-col">
          <button
            type="button"
            aria-label="品牌主页"
            className="flex h-12 items-center justify-center"
          >
            <img src="/favicon.svg" alt="" className="h-7 w-7 object-contain" />
          </button>

          <div className="mt-4 flex-1" />

          <button
            type="button"
            onClick={() => setSettingsOpen(true)}
            aria-label="设置"
            className="px-1.5 py-5 text-gray-300 transition hover:text-gray-100"
          >
            <span className="flex items-center justify-center">
              {settingsIcon}
            </span>
          </button>
        </div>
      </aside>

      <div className="flex-1 min-h-0 min-w-0 flex flex-col px-4 pb-4 pt-5">
        <ResizableSplitPane
          direction={isLg ? 'row' : 'col'}
          minPrimary={isLg ? 300 : 200}
          minSecondary={isLg ? 560 : 240}
          storageKey="bitget-dashboard-studio"
          primary={dashboard}
          secondary={studio}
        />
      </div>

      <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  )
}

export default App
