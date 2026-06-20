import { useEffect, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { getToken } from "./api"
import type { SSEEvent } from "./types"

/**
 * 订阅 /api/events。解析事件并失效相关 React Query 缓存。
 * 返回连接状态：connected=false 时上层应进入降级轮询（见 useRefreshPolling）。
 */
export function useSSE(onEvent?: (e: SSEEvent) => void): boolean {
  const qc = useQueryClient()
  const [connected, setConnected] = useState(false)
  const cbRef = useRef(onEvent)
  // eslint-disable-next-line react-hooks/refs -- 保持回调引用最新(React 文档推荐的 effect 事件模式)
  cbRef.current = onEvent

  useEffect(() => {
    let es: EventSource | null = null
    let reconnect: ReturnType<typeof setTimeout> | undefined
    let stopped = false

    const handle = (ev: SSEEvent) => {
      switch (ev.type) {
        case "new_messages":
          qc.invalidateQueries({ queryKey: ["messages"] })
          break
        case "device":
          qc.invalidateQueries({ queryKey: ["devices"] })
          qc.invalidateQueries({ queryKey: ["status"] })
          break
        case "outbound":
          qc.invalidateQueries({ queryKey: ["outbound"] })
          break
        default:
          break
      }
      cbRef.current?.(ev)
    }

    const start = () => {
      const token = getToken()
      if (!token) return
      es = new EventSource(`/api/events?token=${encodeURIComponent(token)}`)
      es.onopen = () => setConnected(true)
      es.onmessage = (m) => {
        try {
          handle(JSON.parse(m.data) as SSEEvent)
        } catch {
          /* ignore malformed */
        }
      }
      es.onerror = () => {
        setConnected(false)
        es?.close()
        if (!stopped) reconnect = setTimeout(start, 3000)
      }
    }

    start()
    return () => {
      stopped = true
      if (reconnect) clearTimeout(reconnect)
      es?.close()
    }
  }, [qc])

  return connected
}

/**
 * 兜底轮询（docs/guide.md §3.6）：SSE 正常时每 60s 刷新设备/状态；断线时每 30s 刷新设备/状态/收件/发件。
 */
export function useFallbackPolling(connected: boolean): void {
  const qc = useQueryClient()
  useEffect(() => {
    const ms = connected ? 60_000 : 30_000
    const id = setInterval(() => {
      qc.invalidateQueries({ queryKey: ["devices"] })
      qc.invalidateQueries({ queryKey: ["status"] })
      if (!connected) {
        qc.invalidateQueries({ queryKey: ["messages"] })
        qc.invalidateQueries({ queryKey: ["outbound"] })
      }
    }, ms)
    return () => clearInterval(id)
  }, [qc, connected])
}
