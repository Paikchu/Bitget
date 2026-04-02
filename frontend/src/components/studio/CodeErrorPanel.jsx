export default function CodeErrorPanel({ errors, runtimeError, onAiFix, fixing }) {
  const hasErrors = errors.length > 0 || runtimeError
  if (!hasErrors) return null

  const syntaxErrors   = errors.filter(e => e.type === 'syntax')
  const securityErrors = errors.filter(e => e.type === 'security')
  const ifaceErrors    = errors.filter(e => e.type === 'interface')

  const allMessages = [
    ...errors.map(e => e.message),
    ...(runtimeError ? [runtimeError.message] : []),
  ].join('\n')

  return (
    <div className="border-t border-red-900/50 bg-red-950/20">
      <div className="flex items-center justify-between px-3 py-1.5">
        <span className="text-xs text-red-400 font-mono">
          ⚠ {errors.length + (runtimeError ? 1 : 0)} 个错误
        </span>
        <button onClick={() => onAiFix(allMessages)} disabled={fixing}
          className="flex items-center gap-1 px-2.5 py-1 text-xs bg-orange-700 hover:bg-orange-600 disabled:opacity-50 rounded text-white transition-colors">
          {fixing ? (
            <>
              <svg className="animate-spin h-3 w-3" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
              </svg>
              AI 修复中...
            </>
          ) : '🔧 AI 一键修复'}
        </button>
      </div>
      <div className="px-3 pb-2 space-y-1 max-h-32 overflow-y-auto">
        {syntaxErrors.map((e, i) => (
          <div key={i} className="flex gap-2 text-xs font-mono">
            <span className="text-red-500 shrink-0">语法</span>
            {e.line && <span className="text-gray-500 shrink-0">第{e.line}行</span>}
            <span className="text-red-300">{e.message}</span>
          </div>
        ))}
        {securityErrors.map((e, i) => (
          <div key={i} className="flex gap-2 text-xs font-mono">
            <span className="text-orange-500 shrink-0">安全</span>
            {e.line && <span className="text-gray-500 shrink-0">第{e.line}行</span>}
            <span className="text-orange-300">{e.message}</span>
          </div>
        ))}
        {ifaceErrors.map((e, i) => (
          <div key={i} className="flex gap-2 text-xs font-mono">
            <span className="text-yellow-500 shrink-0">接口</span>
            <span className="text-yellow-300">{e.message}</span>
          </div>
        ))}
        {runtimeError && (
          <div className="text-xs font-mono">
            <div className="flex gap-2">
              <span className="text-red-500 shrink-0">运行时</span>
              {runtimeError.line && <span className="text-gray-500 shrink-0">第{runtimeError.line}行</span>}
              <span className="text-red-300">{runtimeError.message}</span>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
