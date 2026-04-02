import useSWR from 'swr'

const fetcher = (url) => fetch(url).then((r) => r.json())

export function useStatus() {
  return useSWR('/api/status', fetcher, { refreshInterval: 5000 })
}

export function useTrades(page = 1, direction = null) {
  const params = new URLSearchParams({ page, limit: 50 })
  if (direction) params.set('direction', direction)
  return useSWR(`/api/trades?${params}`, fetcher, { refreshInterval: 10000 })
}

export function useEquity(days = 30) {
  return useSWR(`/api/equity?days=${days}`, fetcher, { refreshInterval: 10000 })
}

export function useOhlcv(symbol = 'BTC/USDT:USDT', timeframe = '15m') {
  return useSWR(
    `/api/ohlcv?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=200`,
    fetcher,
    { refreshInterval: 60000 },
  )
}
