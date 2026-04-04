import { useEffect, useRef, useState } from 'react'
import { createChart, CrosshairMode, CandlestickSeries, createSeriesMarkers } from 'lightweight-charts'
import { fetchOhlcvPage } from '../hooks/useApi'
import useBotStore from '../store/botStore'

/** @param {'live' | 'backtest'} markerSource - which trades to show as K-line markers */
export default function CandleChart({ symbol = 'BTC/USDT:USDT', timeframe = '15m', markerSource = 'live' }) {
  const containerRef = useRef(null)
  const chartRef = useRef(null)
  const seriesRef = useRef(null)
  const markersRef = useRef(null)
  const candlesRef = useRef([])
  const symbolRef = useRef(symbol)
  const timeframeRef = useRef(timeframe)
  const nextBeforeTsRef = useRef(null)
  const hasMoreRef = useRef(true)
  const loadingMoreRef = useRef(false)
  const preserveRangeRef = useRef(null)
  const initialViewportSetRef = useRef(false)
  const [candles, setCandles] = useState([])
  const liveTrades = useBotStore((s) => s.trades)
  const backtestTrades = useBotStore((s) => s.backtestTrades)
  const trades =
    markerSource === 'backtest'
      ? (Array.isArray(backtestTrades) ? backtestTrades : [])
      : liveTrades

  const mergeCandles = (incoming, existing) => {
    const seen = new Set()
    const merged = []
    const sorted = [...incoming, ...existing].sort((a, b) => a.ts - b.ts)
    sorted.forEach((candle) => {
      if (seen.has(candle.ts)) return
      seen.add(candle.ts)
      merged.push(candle)
    })
    return merged
  }

  // Initialize chart once
  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: { background: { color: '#111827' }, textColor: '#9ca3af' },
      grid: { vertLines: { color: '#1f2937' }, horzLines: { color: '#1f2937' } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#374151' },
      timeScale: { borderColor: '#374151', timeVisible: true },
      width: containerRef.current.clientWidth,
      height: 400,
    })

    // lightweight-charts v5: addSeries(SeriesClass, options)
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderUpColor: '#22c55e',
      borderDownColor: '#ef4444',
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
    })

    // v5: create a markers plugin for this series
    const markers = createSeriesMarkers(series, [])

    chartRef.current = chart
    seriesRef.current = series
    markersRef.current = markers

    const onVisibleLogicalRangeChange = (range) => {
      if (!range) return
      if (range.from > 20) return
      if (!hasMoreRef.current || loadingMoreRef.current || !nextBeforeTsRef.current) return
      const previousRange = chart.timeScale().getVisibleLogicalRange()
      void loadPage(nextBeforeTsRef.current, { prepend: true, previousRange })
    }

    chart.timeScale().subscribeVisibleLogicalRangeChange(onVisibleLogicalRangeChange)

    const ro = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth })
      }
    })
    ro.observe(containerRef.current)

    return () => {
      ro.disconnect()
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onVisibleLogicalRangeChange)
      chart.remove()
    }
  }, [])

  useEffect(() => {
    candlesRef.current = candles
  }, [candles])

  useEffect(() => {
    symbolRef.current = symbol
    timeframeRef.current = timeframe
  }, [symbol, timeframe])

  async function loadPage(beforeTs, { prepend = false, previousRange = null } = {}) {
    if (loadingMoreRef.current) return
    loadingMoreRef.current = true
    try {
      const page = await fetchOhlcvPage({
        symbol: symbolRef.current,
        timeframe: timeframeRef.current,
        limit: 200,
        beforeTs,
      })
      const incoming = Array.isArray(page?.candles) ? page.candles : []
      nextBeforeTsRef.current = page?.page?.next_before_ts ?? null
      hasMoreRef.current = Boolean(page?.page?.has_more)

      setCandles((prev) => {
        const merged = prepend ? mergeCandles(incoming, prev) : mergeCandles(incoming, [])
        if (prepend && previousRange && merged.length > prev.length) {
          const addedCount = merged.length - prev.length
          preserveRangeRef.current = {
            from: previousRange.from + addedCount,
            to: previousRange.to + addedCount,
          }
        } else if (!prepend) {
          initialViewportSetRef.current = false
          preserveRangeRef.current = null
        }
        return merged
      })
    } catch (error) {
      console.error('Failed to load OHLCV page', error)
    } finally {
      loadingMoreRef.current = false
    }
  }

  // Load initial OHLCV page on symbol/timeframe changes
  useEffect(() => {
    setCandles([])
    candlesRef.current = []
    nextBeforeTsRef.current = null
    hasMoreRef.current = true
    loadingMoreRef.current = false
    preserveRangeRef.current = null
    initialViewportSetRef.current = false
    void loadPage(null)
  }, [symbol, timeframe])

  // Push OHLCV candles into chart
  useEffect(() => {
    if (!seriesRef.current) return
    const chartCandles = candles.map((c) => ({
      time: Math.floor(c.ts / 1000),
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }))
    seriesRef.current.setData(chartCandles)
    if (!chartRef.current) return
    if (!initialViewportSetRef.current && chartCandles.length > 0) {
      chartRef.current.timeScale().fitContent()
      initialViewportSetRef.current = true
      return
    }
    if (preserveRangeRef.current) {
      chartRef.current.timeScale().setVisibleLogicalRange(preserveRangeRef.current)
      preserveRangeRef.current = null
    }
  }, [candles])

  // Update trade signal markers
  useEffect(() => {
    if (!markersRef.current) return
    const closed = trades.filter((t) => t.exit_time)
    const markers = []
    closed.forEach((t) => {
      if (t.entry_time)
        markers.push({
          time: Math.floor(new Date(t.entry_time).getTime() / 1000),
          position: 'belowBar',
          color: '#22c55e',
          shape: 'arrowUp',
          text: `${t.direction === 'long' ? 'Long' : 'Short'} @${t.entry_price}`,
        })
      if (t.exit_time)
        markers.push({
          time: Math.floor(new Date(t.exit_time).getTime() / 1000),
          position: 'aboveBar',
          color: '#ef4444',
          shape: 'arrowDown',
          text: `Exit @${t.exit_price}`,
        })
    })
    markers.sort((a, b) => a.time - b.time)
    markersRef.current.setMarkers(markers)
  }, [trades])

  return (
    <div className="bg-gray-900 rounded-lg p-4">
      <h2 className="text-sm font-semibold text-gray-400 mb-3">
        {symbol} · {timeframe} K线图
        {markerSource === 'backtest' && (
          <span className="text-gray-600 font-normal ml-2 text-xs">（回测成交标记）</span>
        )}
        {markerSource === 'live' && (
          <span className="text-gray-600 font-normal ml-2 text-xs">（实盘信号）</span>
        )}
      </h2>
      <div ref={containerRef} />
    </div>
  )
}
