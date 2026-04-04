import { useEffect, useMemo, useState } from 'react'
import { useSWRConfig } from 'swr'
import { testSettingsConnection, updateSettings, useSettings } from '../hooks/useApi'

const secretFields = [
  { key: 'DEEPSEEK_API_KEY', label: 'DeepSeek Key', group: 'system', field: 'deepseek_api_key' },
]

function buildDraft(data) {
  if (!data) return null
  return {
    DEEPSEEK_API_KEY: '',
  }
}

function Field({ label, children, hint }) {
  return (
    <label className="flex flex-col gap-2">
      <span className="text-xs font-medium tracking-[0.18em] uppercase text-gray-500">{label}</span>
      {children}
      {hint ? <span className="text-xs text-gray-500">{hint}</span> : null}
    </label>
  )
}

function TextInput(props) {
  return (
    <input
      {...props}
      className="w-full rounded-2xl border border-gray-800 bg-gray-950/80 px-4 py-3 text-sm text-gray-100 outline-none transition focus:border-blue-500/60 focus:bg-gray-950"
    />
  )
}

export default function SettingsDrawer({ open, onClose }) {
  const { data, isLoading } = useSettings(open)
  const { mutate } = useSWRConfig()
  const [draft, setDraft] = useState(null)
  const [touchedSecrets, setTouchedSecrets] = useState({})
  const [showSecrets, setShowSecrets] = useState({})
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [feedback, setFeedback] = useState(null)

  useEffect(() => {
    if (open && data) {
      setDraft(buildDraft(data))
      setTouchedSecrets({})
      setFeedback(null)
    }
  }, [open, data])

  const dirty = useMemo(() => {
    if (!draft || !data) return false
    return Object.values(touchedSecrets).some(Boolean)
  }, [data, draft, touchedSecrets])

  useEffect(() => {
    if (!open) return undefined
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        if (dirty && !window.confirm('有未保存的修改，确认关闭吗？')) {
          return
        }
        onClose()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [dirty, onClose, open])

  function patchSecret(key, value) {
    setDraft((current) => ({ ...current, [key]: value }))
    setTouchedSecrets((current) => ({ ...current, [key]: true }))
  }

  function attemptClose() {
    if (dirty && !window.confirm('有未保存的修改，确认关闭吗？')) {
      return
    }
    onClose()
  }

  async function handleSave() {
    if (!draft) return
    setSaving(true)
    setFeedback(null)
    try {
      const payload = {}
      for (const field of secretFields) {
        if (touchedSecrets[field.key]) {
          payload[field.key] = draft[field.key]
        }
      }
      const result = await updateSettings(payload)
      await Promise.all([mutate('/api/settings'), mutate('/api/status')])
      setFeedback({
        tone: result.applied ? 'success' : 'warn',
        message: result.applied ? '已保存并生效' : '已保存，运行线程将在下一轮应用新配置',
      })
      setTouchedSecrets({})
    } catch (error) {
      setFeedback({
        tone: 'error',
        message: error.message || '保存失败',
      })
    } finally {
      setSaving(false)
    }
  }

  async function handleTest() {
    if (!draft) return
    setTesting(true)
    setFeedback(null)
    try {
      const payload = {}
      for (const field of secretFields) {
        if (touchedSecrets[field.key]) {
          payload[field.key] = draft[field.key]
        }
      }
      const result = await testSettingsConnection(payload)
      setFeedback({
        tone: result.deepseek?.ok ? 'success' : 'warn',
        message: result.deepseek?.ok
          ? 'DeepSeek 连接成功'
          : `DeepSeek 连接失败${result.deepseek?.error ? `：${result.deepseek.error}` : ''}`,
      })
    } catch (error) {
      setFeedback({
        tone: 'error',
        message: error.message || '测试失败',
      })
    } finally {
      setTesting(false)
    }
  }

  if (!open) return null

  const feedbackClass = feedback?.tone === 'error'
    ? 'border-red-500/30 bg-red-500/10 text-red-200'
    : feedback?.tone === 'warn'
      ? 'border-amber-500/30 bg-amber-500/10 text-amber-100'
      : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-100'

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button
        type="button"
        aria-label="关闭设置"
        className="absolute inset-0 bg-black/55 backdrop-blur-[2px]"
        onClick={attemptClose}
      />

      <aside className="relative z-10 flex h-full w-full max-w-[540px] flex-col border-l border-gray-800 bg-[linear-gradient(180deg,rgba(7,10,22,0.98)_0%,rgba(3,6,16,0.99)_100%)] shadow-2xl">
        <div className="flex items-start justify-between border-b border-gray-800 px-6 py-5">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.36em] text-blue-300/70">DeepSeek API</p>
            <h2 className="mt-2 text-xl font-semibold text-white">设置</h2>
            <p className="mt-1 text-sm text-gray-400">配置当前使用的 DeepSeek API</p>
          </div>
          <button
            type="button"
            onClick={attemptClose}
            className="rounded-2xl border border-gray-800 bg-gray-900/80 px-3 py-2 text-sm text-gray-300 transition hover:border-gray-700 hover:text-white"
          >
            关闭
          </button>
        </div>

        <div className="flex-1 space-y-6 overflow-y-auto px-6 py-6">
          <section className="space-y-4 rounded-[28px] border border-gray-800 bg-gray-900/45 p-5">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.3em] text-blue-300/70">DeepSeek API</p>
              <h3 className="mt-2 text-base font-semibold text-white">DeepSeek API 配置</h3>
            </div>

            <Field
              label="DeepSeek Key"
              hint={
                data?.system?.deepseek_api_key?.is_set && !touchedSecrets.DEEPSEEK_API_KEY
                  ? `当前已配置：${data.system.deepseek_api_key.value}`
                  : '留空表示保持当前值，输入空字符串后保存可清空'
              }
            >
              <div className="flex gap-2">
                <TextInput
                  type={showSecrets.DEEPSEEK_API_KEY ? 'text' : 'password'}
                  value={draft?.DEEPSEEK_API_KEY ?? ''}
                  placeholder={data?.system?.deepseek_api_key?.value || '未配置'}
                  onChange={(event) => patchSecret('DEEPSEEK_API_KEY', event.target.value)}
                />
                <button
                  type="button"
                  onClick={() => setShowSecrets((current) => ({ ...current, DEEPSEEK_API_KEY: !current.DEEPSEEK_API_KEY }))}
                  className="rounded-2xl border border-gray-800 bg-gray-950/80 px-3 text-sm text-gray-300"
                >
                  {showSecrets.DEEPSEEK_API_KEY ? '隐藏' : '显示'}
                </button>
              </div>
            </Field>
          </section>

          {feedback ? (
            <div className={`rounded-2xl border px-4 py-3 text-sm ${feedbackClass}`}>
              {feedback.message}
            </div>
          ) : null}

          {isLoading ? (
            <div className="rounded-2xl border border-gray-800 bg-gray-900/45 px-4 py-3 text-sm text-gray-400">
              正在加载设置...
            </div>
          ) : null}
        </div>

        <div className="flex items-center justify-between border-t border-gray-800 px-6 py-5">
          <button
            type="button"
            onClick={handleTest}
            disabled={testing || saving || !draft}
            className="rounded-2xl border border-gray-800 bg-gray-900/80 px-4 py-3 text-sm font-medium text-gray-200 transition disabled:cursor-not-allowed disabled:opacity-50"
          >
            {testing ? '测试中...' : '测试连接'}
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || testing || !draft}
            className="rounded-2xl border border-blue-500/30 bg-blue-500/15 px-4 py-3 text-sm font-medium text-blue-100 transition hover:bg-blue-500/20 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving ? '保存中...' : '保存'}
          </button>
        </div>
      </aside>
    </div>
  )
}
