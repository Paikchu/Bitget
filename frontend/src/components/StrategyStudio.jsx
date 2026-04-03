import { useState, useRef, useCallback, useEffect } from 'react'
import MarkdownEditor, { DEFAULT_MARKDOWN } from './studio/MarkdownEditor'
import CodeEditor from './studio/CodeEditor'
import BacktestResults from './studio/BacktestResults'
import { useCodeValidation } from '../hooks/useCodeValidation'
import CodeErrorPanel from './studio/CodeErrorPanel'

const API = '/api/strategy'
const DEFAULT_PARAMS = {
  symbol: 'BTC/USDT:USDT',
  timeframe: '15m',
  days: 90,
  initial_equity: 10000,
  leverage: 5,
  margin_pct: 100,
  fee_rate: 0.0005,
  squeeze_threshold: 0.35,
}

/** Parse sandbox / Python errors for the error panel (line hint when traceback present). */
function parseRuntimeError(err) {
  if (!err || typeof err !== 'string') return null
  const matches = [...err.matchAll(/File "<strategy>",\s*line\s*(\d+)/g)]
  const last = matches.length ? parseInt(matches[matches.length - 1][1], 10) : null
  const lines = err.trim().split('\n')
  const tail = lines[lines.length - 1] || err
  return {
    line: last,
    message: tail.length > 280 ? `${tail.slice(0, 280)}…` : tail,
    full_traceback: err.length > 4000 ? `${err.slice(0, 4000)}…` : err,
  }
}

export default function StrategyStudio() {
  const [markdown, setMarkdown]         = useState(DEFAULT_MARKDOWN)
  const [code, setCode]                 = useState('')
  const [generating, setGenerating]     = useState(false)
  const [backtestLoading, setBtLoading] = useState(false)
  const [backtestResult, setBtResult]   = useState(null)
  const [backtestError, setBtError]     = useState(null)
  const [params, setParams]             = useState(DEFAULT_PARAMS)
  const [genError, setGenError]         = useState(null)
  const [runtimeError, setRuntimeError] = useState(null)
  const [fixing, setFixing]             = useState(false)
  const pollRef = useRef(null)

  const { errors: codeErrors, monacoMarkers } = useCodeValidation(code)

  // Default: load built-in MA-squeeze strategy (markdown + code) into both editors
  useEffect(() => {
    fetch(`${API}/builtin/ma_squeeze`)
      .then(r => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.markdown) setMarkdown(data.markdown)
        if (data?.code) setCode(data.code)
      })
      .catch(() => {})
  }, [])

  const handleGenerate = useCallback(async () => {
    setGenerating(true)
    setGenError(null)
    try {
      const res = await fetch(`${API}/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ markdown }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '生成失败')
      setCode(data.code)
    } catch (e) {
      setGenError(e.message)
    } finally {
      setGenerating(false)
    }
  }, [markdown])

  const handleRegenerate = useCallback(() => {
    setCode('')
    handleGenerate()
  }, [handleGenerate])

  const handleLoadBuiltin = useCallback(async () => {
    try {
      const res = await fetch(`${API}/builtin/ma_squeeze`)
      const data = await res.json()
      setMarkdown(data.markdown)
      setCode(data.code)
    } catch (e) {
      console.error('Failed to load builtin strategy', e)
    }
  }, [])

  const handleAiFix = useCallback(async (errorMessage) => {
    if (!code) return
    setFixing(true)
    try {
      const res = await fetch('/api/strategy/fix', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          code,
          error_message: errorMessage,
          error_type: runtimeError ? 'runtime' : 'static',
        }),
      })
      const data = await res.json()
      if (data.code) { setCode(data.code); setRuntimeError(null) }
    } catch (e) {
      console.error('AI fix failed:', e)
    } finally {
      setFixing(false)
    }
  }, [code, runtimeError])

  const handleBacktest = useCallback(async () => {
    if (!code?.trim()) { setBtError('请先生成或编写策略代码'); return }
    clearTimeout(pollRef.current)
    setBtLoading(true)
    setBtResult(null)
    setBtError(null)
    setRuntimeError(null)
    try {
      const res = await fetch(`${API}/backtest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategy_code: code, ...params }),
      })
      const { job_id } = await res.json()
      const poll = async () => {
        try {
          const r = await fetch(`${API}/backtest/${job_id}`)
          const job = await r.json()
          if (job.status === 'running') {
            pollRef.current = setTimeout(poll, 2000)
          } else if (job.status === 'done') {
            setBtResult(job)
            setBtLoading(false)
            setRuntimeError(null)
            document.getElementById('bt-results')?.scrollIntoView({ behavior: 'smooth' })
          } else {
            const msg = job.error || '回测失败'
            setBtError(msg)
            setRuntimeError(parseRuntimeError(msg))
            setBtLoading(false)
          }
        } catch { setBtError('轮询失败'); setBtLoading(false) }
      }
      pollRef.current = setTimeout(poll, 1500)
    } catch (e) {
      setBtError(e.message); setBtLoading(false)
    }
  }, [code, params])

  return (
    <div className="flex flex-col" style={{ height: 'calc(100vh - 48px)' }}>
      {/* Split editor panes */}
      <div className="flex flex-1 min-h-0">
        {/* Left: Markdown */}
        <div className="w-1/2 flex flex-col border-r border-gray-800">
          <div className="flex-1 min-h-0"><MarkdownEditor value={markdown} onChange={setMarkdown} /></div>
          <div className="flex items-center gap-2 px-3 py-2 bg-gray-900 border-t border-gray-800">
            <button onClick={handleGenerate} disabled={generating || !markdown?.trim()}
              className="flex items-center gap-1.5 px-4 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm rounded-md font-medium transition-colors">
              {generating ? (
                <><svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>生成中...</>
              ) : '▶ 生成代码'}
            </button>
            {code && <button onClick={handleRegenerate} disabled={generating}
              className="px-3 py-1.5 text-gray-400 hover:text-gray-200 text-sm border border-gray-700 hover:border-gray-500 rounded-md transition-colors">
              重新生成
            </button>}
            <button onClick={handleLoadBuiltin}
              className="px-3 py-1.5 text-gray-400 hover:text-gray-200 text-sm border border-gray-700 hover:border-gray-500 rounded-md transition-colors">
              📦 加载内置策略
            </button>
            {genError && <span className="text-red-400 text-xs ml-2">{genError}</span>}
          </div>
        </div>
        {/* Right: Code */}
        <div className="w-1/2 flex flex-col">
          <div className="flex-1 min-h-0"><CodeEditor value={code} onChange={setCode} generating={generating} monacoMarkers={monacoMarkers} /></div>
          <CodeErrorPanel errors={codeErrors} runtimeError={runtimeError} onAiFix={handleAiFix} fixing={fixing} />
          {code && (
            <div className="flex items-center gap-2 px-3 py-2 bg-gray-900 border-t border-gray-800">
              <button onClick={() => navigator.clipboard.writeText(code)}
                className="px-3 py-1.5 text-gray-400 hover:text-gray-200 text-xs border border-gray-700 hover:border-gray-500 rounded-md transition-colors">
                📋 复制代码
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Backtest params bar */}
      <div className="flex items-center gap-3 px-4 py-2.5 bg-gray-900 border-t border-gray-700 flex-wrap">
        <span className="text-xs text-gray-500 font-mono">回测参数</span>
        <label className="flex items-center gap-1.5 text-xs text-gray-400">
          交易对
          <input value={params.symbol} onChange={e => setParams(p => ({...p, symbol: e.target.value}))}
            className="bg-gray-800 border border-gray-700 rounded px-2 py-1 w-36 text-gray-200 text-xs" />
        </label>
        <label className="flex items-center gap-1.5 text-xs text-gray-400">
          天数
          <input type="number" min="7" max="365" value={params.days} onChange={e => setParams(p => ({...p, days: +e.target.value}))}
            className="bg-gray-800 border border-gray-700 rounded px-2 py-1 w-16 text-gray-200 text-xs" />
        </label>
        <label className="flex items-center gap-1.5 text-xs text-gray-400">
          杠杆
          <input type="number" min="1" max="50" value={params.leverage} onChange={e => setParams(p => ({...p, leverage: +e.target.value}))}
            className="bg-gray-800 border border-gray-700 rounded px-2 py-1 w-14 text-gray-200 text-xs" />
          <span className="text-gray-600">x</span>
        </label>
        <label className="flex items-center gap-1.5 text-xs text-gray-400">
          初始资金
          <input type="number" min="100" value={params.initial_equity} onChange={e => setParams(p => ({...p, initial_equity: +e.target.value}))}
            className="bg-gray-800 border border-gray-700 rounded px-2 py-1 w-20 text-gray-200 text-xs" />
          <span className="text-gray-600">USDT</span>
        </label>
        <label className="flex items-center gap-1.5 text-xs text-gray-400">
          Squeeze%
          <input type="number" step="0.01" min="0.05" max="5" value={params.squeeze_threshold} onChange={e => setParams(p => ({...p, squeeze_threshold: +e.target.value}))}
            className="bg-gray-800 border border-gray-700 rounded px-2 py-1 w-16 text-gray-200 text-xs" />
        </label>
        <button onClick={handleBacktest} disabled={backtestLoading || !code}
          className="ml-auto flex items-center gap-1.5 px-4 py-1.5 bg-green-700 hover:bg-green-600 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm rounded-md font-medium transition-colors">
          {backtestLoading ? (
            <><svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>运行中...</>
          ) : '▶ 运行回测'}
        </button>
      </div>

      {/* Backtest results */}
      <div id="bt-results" className="overflow-y-auto max-h-[45vh] border-t border-gray-800">
        <BacktestResults result={backtestResult} loading={backtestLoading} error={backtestError} />
      </div>
    </div>
  )
}
