import { useCallback, useEffect, useRef, useState } from 'react'

const STORAGE_PREFIX = 'bitget-split'

function clientCoord(ev, axis) {
  const t = ev.touches?.[0]
  if (t) return axis === 'x' ? t.clientX : t.clientY
  return axis === 'x' ? ev.clientX : ev.clientY
}

function readStoredNumber(key) {
  if (typeof window === 'undefined') return null
  try {
    const v = localStorage.getItem(key)
    if (v == null) return null
    const n = parseFloat(v)
    return Number.isFinite(n) ? n : null
  } catch {
    return null
  }
}

function writeStoredNumber(key, n) {
  try {
    localStorage.setItem(key, String(Math.round(n)))
  } catch {
    /* ignore */
  }
}

/**
 * @param {'row' | 'col'} direction - row: left | divider | right; col: top | divider | bottom
 * @param {number} minPrimary - min size (px) of first pane
 * @param {number} minSecondary - min size (px) of second pane
 * @param {string} [storageKey] - base key; persisted as `${storageKey}-row` / `${storageKey}-col`
 */
export default function ResizableSplitPane({
  direction,
  minPrimary = 300,
  minSecondary = 320,
  storageKey = STORAGE_PREFIX,
  className = '',
  primary: primaryChild,
  secondary: secondaryChild,
}) {
  const wrapRef = useRef(null)
  const [primaryPx, setPrimaryPx] = useState(null)
  const draggingRef = useRef(false)

  const clampPrimary = useCallback(
    (raw, containerSize) => {
      if (containerSize <= 0) return minPrimary
      const maxPrimary = Math.max(minPrimary, containerSize - minSecondary)
      return Math.min(maxPrimary, Math.max(minPrimary, raw))
    },
    [minPrimary, minSecondary],
  )

  const initFromContainer = useCallback(() => {
    const el = wrapRef.current
    if (!el) return
    const size = direction === 'row' ? el.clientWidth : el.clientHeight
    const stored = readStoredNumber(`${storageKey}-${direction}`)
    const next = clampPrimary(stored != null ? stored : size * 0.5, size)
    setPrimaryPx(next)
  }, [direction, storageKey, clampPrimary])

  useEffect(() => {
    setPrimaryPx(null)
  }, [direction])

  useEffect(() => {
    initFromContainer()
  }, [initFromContainer])

  useEffect(() => {
    const el = wrapRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => {
      const size = direction === 'row' ? el.clientWidth : el.clientHeight
      setPrimaryPx((prev) => (prev == null ? prev : clampPrimary(prev, size)))
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [direction, clampPrimary])

  useEffect(() => {
    const onWinResize = () => {
      const el = wrapRef.current
      if (!el) return
      const size = direction === 'row' ? el.clientWidth : el.clientHeight
      setPrimaryPx((prev) => (prev == null ? prev : clampPrimary(prev, size)))
    }
    window.addEventListener('resize', onWinResize)
    return () => window.removeEventListener('resize', onWinResize)
  }, [direction, clampPrimary])

  const onDividerDown = useCallback(
    (e) => {
      e.preventDefault()
      draggingRef.current = true
      const el = wrapRef.current
      if (!el || primaryPx == null) return
      const startPrimary = primaryPx
      const startCoord = direction === 'row' ? clientCoord(e, 'x') : clientCoord(e, 'y')

      const onMove = (ev) => {
        if (!draggingRef.current) return
        ev.preventDefault?.()
        const coord = direction === 'row' ? clientCoord(ev, 'x') : clientCoord(ev, 'y')
        const delta = coord - startCoord
        const size = direction === 'row' ? el.clientWidth : el.clientHeight
        const next = clampPrimary(startPrimary + delta, size)
        setPrimaryPx(next)
        writeStoredNumber(`${storageKey}-${direction}`, next)
      }

      const onUp = () => {
        draggingRef.current = false
        window.removeEventListener('mousemove', onMove)
        window.removeEventListener('mouseup', onUp)
        window.removeEventListener('touchmove', onMove, { capture: true })
        window.removeEventListener('touchend', onUp, { capture: true })
      }

      window.addEventListener('mousemove', onMove)
      window.addEventListener('mouseup', onUp)
      window.addEventListener('touchmove', onMove, { passive: false, capture: true })
      window.addEventListener('touchend', onUp, { capture: true })
    },
    [direction, primaryPx, clampPrimary, storageKey],
  )

  const flexDir = direction === 'row' ? 'flex-row' : 'flex-col'
  const primaryStyle =
    primaryPx != null
      ? direction === 'row'
        ? { width: primaryPx, minWidth: minPrimary, flexShrink: 0 }
        : { height: primaryPx, minHeight: minPrimary, flexShrink: 0 }
      : direction === 'row'
        ? { minWidth: minPrimary, flex: '1 1 50%' }
        : { minHeight: minPrimary, flex: '1 1 45%' }
  const secondaryStyle =
    direction === 'row'
      ? { minWidth: minSecondary }
      : { minHeight: minSecondary }

  const cursor = direction === 'row' ? 'cursor-col-resize' : 'cursor-row-resize'
  const dividerClass =
    direction === 'row'
      ? `w-1.5 shrink-0 flex justify-center bg-gray-900 hover:bg-gray-800 ${cursor} group select-none touch-none`
      : `h-1.5 shrink-0 flex flex-col justify-center bg-gray-900 hover:bg-gray-800 ${cursor} group select-none touch-none`

  return (
    <div ref={wrapRef} className={`flex ${flexDir} flex-1 min-h-0 min-w-0 w-full ${className}`}>
      <div
        className="min-h-0 min-w-0 overflow-hidden flex flex-col"
        style={primaryStyle}
      >
        {primaryChild}
      </div>
      <div
        role="separator"
        aria-orientation={direction === 'row' ? 'vertical' : 'horizontal'}
        className={dividerClass}
        onMouseDown={onDividerDown}
        onTouchStart={onDividerDown}
      >
        <span
          className={
            direction === 'row'
              ? 'w-px h-full bg-gray-800 group-hover:bg-gray-600'
              : 'h-px w-full bg-gray-800 group-hover:bg-gray-600'
          }
        />
      </div>
      <div
        className="flex-1 min-h-0 min-w-0 overflow-y-auto overflow-x-hidden overscroll-y-contain flex flex-col"
        style={secondaryStyle}
      >
        {secondaryChild}
      </div>
    </div>
  )
}
