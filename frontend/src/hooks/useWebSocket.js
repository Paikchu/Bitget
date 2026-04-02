import { useEffect, useRef } from 'react'
import useBotStore from '../store/botStore'

export function useWebSocket() {
  const { updateTrade, updateEquity, setStatus, setWsConnected } = useBotStore()
  const wsRef = useRef(null)
  const timerRef = useRef(null)

  useEffect(() => {
    function connect() {
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
      const ws = new WebSocket(`${protocol}//${location.host}/ws`)
      wsRef.current = ws

      ws.onopen = () => setWsConnected(true)

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          switch (msg.type) {
            case 'trade_open':
            case 'trade_close':
              updateTrade(msg.data)
              break
            case 'equity_update':
              updateEquity(msg.data)
              break
            case 'bot_status':
              setStatus(msg.data)
              break
          }
        } catch (_) {}
      }

      ws.onclose = () => {
        setWsConnected(false)
        timerRef.current = setTimeout(connect, 3000)
      }

      ws.onerror = () => ws.close()
    }

    connect()
    return () => {
      clearTimeout(timerRef.current)
      wsRef.current?.close()
    }
  }, [])
}
