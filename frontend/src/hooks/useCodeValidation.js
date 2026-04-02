import { useState, useEffect, useRef, useCallback } from 'react'

/**
 * Debounced real-time strategy code validation.
 * Returns errors array and Monaco-compatible markers.
 */
export function useCodeValidation(code) {
  const [errors, setErrors] = useState([])
  const [validating, setValidating] = useState(false)
  const timerRef = useRef(null)

  const validate = useCallback(async (src) => {
    if (!src || !src.trim()) { setErrors([]); return }
    setValidating(true)
    try {
      const res = await fetch('/api/strategy/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: src }),
      })
      const data = await res.json()
      setErrors(data.errors || [])
    } catch { /* silent */ }
    finally { setValidating(false) }
  }, [])

  useEffect(() => {
    clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => validate(code), 800)
    return () => clearTimeout(timerRef.current)
  }, [code, validate])

  // Convert to Monaco marker format (severity: 8=Error, 4=Warning)
  const monacoMarkers = errors
    .filter(e => e.line != null)
    .map(e => ({
      startLineNumber: e.line,
      endLineNumber: e.end_line || e.line,
      startColumn: (e.col ?? 0) + 1,
      endColumn: 1000,
      message: e.message,
      severity: e.severity === 'error' ? 8 : 4,
    }))

  return { errors, validating, monacoMarkers }
}
