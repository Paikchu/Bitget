import { useEffect, useRef } from 'react'
import { createChart, CrosshairMode, CandlestickSeries, createSeriesMarkers } from 'lightweight-charts'
import { useOhlcv } from '../hooks/useApi'
import useBotStore from '../store/botStore'

/** @param {'live' | 'backtest'} markerSource - which trades to show as K-line markers */
export default function CandleChart({ symbol = 'BTC/USDT:USDT', timeframe = '15m', markerSource = 'live' }) {
  const containerRef = useRef(null)
  const chartRef = useRef(null)
  const seriesRef = useRef(null)
  const markersRef = useRef(null)
  const { data } = useOhlcv(symbol, timeframe)
  const liveTrades = useBotStore((s) => s.trades)
  const backtestTrades = useBotStore((s) => s.backtestTrades)
  const trades =
    markerSource === 'backtest'
      ? (Array.isArray(backtestTrades) ? backtestTrades : [])
      : liveTrades

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

    const ro = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth })
      }
    })
    ro.observe(containerRef.current)

    return () => {
      ro.disconnect()
      chart.remove()
    }
  }, [])

  // Load OHLCV candles
  useEffect(() => {
    if (!seriesRef.current || !data?.candles) return
    const candles = data.candles.map((c) => ({
      time: Math.floor(c.ts / 1000),
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }))
    seriesRef.current.setData(candles)
  }, [data])

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
