import { useState, useCallback, useEffect, startTransition } from 'react'
import MarkdownEditor, { DEFAULT_MARKDOWN } from './studio/MarkdownEditor'
import CodeEditor from './studio/CodeEditor'
import BacktestResults from './studio/BacktestResults'
import { useCodeValidation } from '../hooks/useCodeValidation'
import CodeErrorPanel from './studio/CodeErrorPanel'
import VersionHistoryPanel from './studio/VersionHistoryPanel'
import useBotStore from '../store/botStore'

const API = '/api/strategy'

/** Editor stack needs a bounded height so Monaco works inside the scrollable right pane. */
const EDITOR_BLOCK_CLASS = 'flex-1 min-h-[320px] min-w-0 w-full flex flex-col'

export default function StrategyStudio() {
  const [workbenchView, setWorkbenchView] = useState('docs')
  const [markdown, setMarkdown] = useState(DEFAULT_MARKDOWN)
  const [code, setCode] = useState('')
  const [generating, setGenerating] = useState(false)
  const [backtestLoading, setBtLoading] = useState(false)
  const [backtestResult, setBtResult] = useState(null)
  const [backtestError, setBtError] = useState(null)
  const [genError, setGenError] = useState(null)
  const [runtimeError, setRuntimeError] = useState(null)
  const [fixing, setFixing] = useState(false)
  const [versionsLoading, setVersionsLoading] = useState(false)
  const [versionsError, setVersionsError] = useState(null)
  const [restoringVersionId, setRestoringVersionId] = useState(null)
  const setStrategyCode = useBotStore((s) => s.setStrategyCode)
  const strategyVersions = useBotStore((s) => s.strategyVersions)
  const currentStrategyVersion = useBotStore((s) => s.currentStrategyVersion)
  const setStrategyVersions = useBotStore((s) => s.setStrategyVersions)
  const setCurrentStrategyVersion = useBotStore((s) => s.setCurrentStrategyVersion)
  const isVersionDrawerOpen = useBotStore((s) => s.isVersionDrawerOpen)
  const openVersionDrawer = useBotStore((s) => s.openVersionDrawer)
  const closeVersionDrawer = useBotStore((s) => s.closeVersionDrawer)
  const versionHistoryNonce = useBotStore((s) => s.versionHistoryNonce)

  const { errors: codeErrors, monacoMarkers } = useCodeValidation(code)

  const fetchVersions = useCallback(async () => {
    setVersionsLoading(true)
    setVersionsError(null)
    try {
      const res = await fetch(`${API}/versions`)
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '加载版本历史失败')
      startTransition(() => {
        setStrategyVersions(Array.isArray(data) ? data : [])
      })
    } catch (e) {
      setVersionsError(e.message)
    } finally {
      setVersionsLoading(false)
    }
  }, [setStrategyVersions])

  useEffect(() => {
    fetch(`${API}/builtin/ma_squeeze`)
      .then(r => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.markdown) setMarkdown(data.markdown)
        if (data?.code) {
          setCode(data.code)
          setStrategyCode(data.code)
        }
      })
      .catch(() => {})
  }, [setStrategyCode])

  useEffect(() => {
    fetchVersions()
  }, [fetchVersions, versionHistoryNonce])

  useEffect(() => {
    setStrategyCode(code || '')
  }, [code, setStrategyCode])

  useEffect(() => {
    if (!currentStrategyVersion?.saved_code) return
    if (
      code !== currentStrategyVersion.saved_code ||
      markdown !== currentStrategyVersion.saved_markdown
    ) {
      setCurrentStrategyVersion(null)
    }
  }, [code, currentStrategyVersion, markdown, setCurrentStrategyVersion])

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
      setCurrentStrategyVersion(
        data.version
          ? {
              ...data.version,
              saved_code: data.code,
              saved_markdown: markdown,
            }
          : null,
      )
      closeVersionDrawer()
      fetchVersions()
    } catch (e) {
      setGenError(e.message)
    } finally {
      setGenerating(false)
    }
  }, [closeVersionDrawer, fetchVersions, markdown, setCurrentStrategyVersion])

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
      setCurrentStrategyVersion(null)
      closeVersionDrawer()
    } catch (e) {
      console.error('Failed to load builtin strategy', e)
    }
  }, [closeVersionDrawer, setCurrentStrategyVersion])

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

  const handleRestoreVersion = useCallback(async (version) => {
    setRestoringVersionId(version.id)
    setVersionsError(null)
    try {
      const res = await fetch(`${API}/versions/${version.id}`)
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '载入版本失败')
      setMarkdown(data.markdown || '')
      setCode(data.code || '')
      setCurrentStrategyVersion({
        id: data.id,
        version_no: data.version_no,
        title: data.title,
        source: data.source,
        model: data.model,
        created_at: data.created_at,
        parent_version_id: data.parent_version_id,
        saved_code: data.code || '',
        saved_markdown: data.markdown || '',
      })
      setWorkbenchView('code')
      closeVersionDrawer()
    } catch (e) {
      setVersionsError(e.message)
    } finally {
      setRestoringVersionId(null)
    }
  }, [closeVersionDrawer, setCurrentStrategyVersion])

  const tabBtn = (id, label) => (
    <button
      type="button"
      onClick={() => setWorkbenchView(id)}
      role="tab"
      aria-selected={workbenchView === id}
      id={`studio-tab-${id}`}
      className={`shrink-0 px-3 py-1 rounded text-xs font-medium transition-colors ${
      workbenchView === id
        ? 'bg-gray-600 text-white'
        : 'text-gray-400 hover:text-white'
    }`}
    >
      {label}
    </button>
  )

  return (
    <div className="relative flex h-full min-h-0 w-full min-w-0 flex-col overflow-hidden px-4 pb-6 pt-0 box-border">
      <div className="sticky top-0 z-10 mb-2 border-b border-gray-800 bg-gray-950 pb-2 pt-0">
        <div className="relative rounded-lg border border-gray-800 bg-gray-900 px-2 py-1.5 pr-[158px]">
          <h1 className="min-w-0 truncate px-2 text-sm font-semibold text-gray-400">策略工作区</h1>
          <div
            className="absolute right-2 top-1/2 flex -translate-y-1/2 shrink-0 gap-0.5 rounded bg-gray-800 p-0.5"
            role="tablist"
            aria-label="策略页面切换"
          >
            {tabBtn('docs', '策略文档')}
            {tabBtn('code', '策略代码')}
          </div>
        </div>
      </div>

      <div
        className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-gray-800 bg-gray-950/60"
        role="tabpanel"
        aria-labelledby={`studio-tab-${workbenchView}`}
      >
        {workbenchView === 'docs' ? (
          <>
            <div className={EDITOR_BLOCK_CLASS}>
              <MarkdownEditor value={markdown} onChange={setMarkdown} />
            </div>
            <div className="flex items-center gap-2 px-3 py-2 bg-gray-900 border-t border-gray-800 flex-wrap">
              <button
                onClick={handleGenerate}
                disabled={generating || !markdown?.trim()}
                className="flex items-center gap-1.5 px-4 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm rounded-md font-medium transition-colors"
              >
                {generating ? (
                  <>
                    <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                    生成中...
                  </>
                ) : (
                  '生成代码'
                )}
              </button>
              {code && (
                <button
                  onClick={handleRegenerate}
                  disabled={generating}
                  className="px-3 py-1.5 text-gray-400 hover:text-gray-200 text-sm border border-gray-700 hover:border-gray-500 rounded-md transition-colors"
                >
                  重新生成
                </button>
              )}
              <button
                onClick={handleLoadBuiltin}
                className="px-3 py-1.5 text-gray-400 hover:text-gray-200 text-sm border border-gray-700 hover:border-gray-500 rounded-md transition-colors"
              >
                加载内置策略
              </button>
              <button
                onClick={openVersionDrawer}
                className="px-3 py-1.5 text-gray-400 hover:text-gray-200 text-sm border border-gray-700 hover:border-gray-500 rounded-md transition-colors"
              >
                历史版本
              </button>
              {genError && <span className="text-red-400 text-xs ml-2">{genError}</span>}
            </div>
          </>
        ) : (
          <>
            <div className={EDITOR_BLOCK_CLASS}>
              <CodeEditor
                value={code}
                onChange={setCode}
                generating={generating}
                monacoMarkers={monacoMarkers}
              />
            </div>
            <CodeErrorPanel errors={codeErrors} runtimeError={runtimeError} onAiFix={handleAiFix} fixing={fixing} />
            {code && (
              <div className="flex items-center gap-2 px-3 py-2 bg-gray-900 border-t border-gray-800">
                <button
                  type="button"
                  onClick={() => navigator.clipboard.writeText(code)}
                  className="px-3 py-1.5 text-gray-400 hover:text-gray-200 text-xs border border-gray-700 hover:border-gray-500 rounded-md transition-colors"
                >
                  复制代码
                </button>
              </div>
            )}
          </>
        )}
      </div>

      <VersionHistoryPanel
        open={isVersionDrawerOpen}
        versions={strategyVersions}
        currentVersion={currentStrategyVersion}
        loading={versionsLoading}
        restoring={Boolean(restoringVersionId)}
        error={versionsError}
        onRefresh={fetchVersions}
        onRestore={handleRestoreVersion}
        onClose={closeVersionDrawer}
      />

      {(backtestLoading || backtestResult || backtestError) && (
        <div id="bt-results" className="max-h-[40%] shrink-0 overflow-y-auto border-t border-gray-800">
          <BacktestResults result={backtestResult} loading={backtestLoading} error={backtestError} />
        </div>
      )}
    </div>
  )
}
