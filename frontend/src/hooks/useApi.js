import useSWR from 'swr'

async function fetcher(url) {
  const response = await fetch(url)
  const data = await response.json()
  if (!response.ok) {
    throw new Error(data.detail || '请求失败')
  }
  return data
}

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

export function useSettings(enabled = true) {
  return useSWR(enabled ? '/api/settings' : null, fetcher)
}

export async function updateSettings(payload) {
  const response = await fetch('/api/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  const data = await response.json()
  if (!response.ok) {
    throw new Error(data.detail || '保存失败')
  }
  return data
}

export async function testSettingsConnection(payload) {
  const response = await fetch('/api/settings/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  const data = await response.json()
  if (!response.ok) {
    throw new Error(data.detail || '测试失败')
  }
  return data
}
