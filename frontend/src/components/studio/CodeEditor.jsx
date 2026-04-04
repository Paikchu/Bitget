import Editor from '@monaco-editor/react'
import { useRef, useEffect } from 'react'

const PLACEHOLDER_CODE = `# 点击左侧的「▶ 生成代码」按钮，AI 将根据你的策略描述自动生成 Python 代码

import numpy as np
import pandas as pd

def add_indicators(df):
    out = df.copy()
    # 在这里添加指标计算
    return out

def get_signal(df, i, params):
    return {
        'long_entry':  False,
        'short_entry': False,
        'close_long':  False,
        'close_short': False,
    }
`

export default function CodeEditor({ value, onChange, generating, monacoMarkers = [] }) {
  const editorRef = useRef(null)
  const monacoRef = useRef(null)

  useEffect(() => {
    if (!editorRef.current || !monacoRef.current) return
    const model = editorRef.current.getModel()
    if (!model) return
    monacoRef.current.editor.setModelMarkers(model, 'strategy-validator', monacoMarkers)
  }, [monacoMarkers])

  function handleMount(editor, monaco) {
    editorRef.current = editor
    monacoRef.current = monaco
  }

  return (
    <div className="flex flex-col h-full relative">
      <div className="flex-1 min-h-0">
        <Editor
          height="100%"
          defaultLanguage="python"
          value={value || PLACEHOLDER_CODE}
          onChange={onChange}
          theme="vs-dark"
          onMount={handleMount}
          options={{
            fontSize: 13,
            lineHeight: 20,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            padding: { top: 12, bottom: 12 },
            readOnly: generating,
          }}
        />
      </div>
      {generating && (
        <div className="absolute inset-0 bg-gray-950/60 flex items-center justify-center pointer-events-none">
          <div className="bg-gray-900 border border-blue-800 rounded-lg px-6 py-4 text-blue-400 text-sm flex items-center gap-2">
            <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
            </svg>
            DeepSeek 正在分析策略文档...
          </div>
        </div>
      )}
    </div>
  )
}
