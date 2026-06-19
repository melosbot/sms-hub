import { useEffect, useState } from "react"

// 跨标签页通信：从收件箱「回复」时预填发送表单。
export interface ComposeDraft {
  to?: string
  text?: string
  sim_id?: string
}

let current: ComposeDraft | null = null
const subs = new Set<() => void>()

export function setComposeDraft(d: ComposeDraft | null): void {
  current = d
  subs.forEach((f) => f())
}

export function useComposeDraft(): ComposeDraft | null {
  const [d, setD] = useState<ComposeDraft | null>(current)
  useEffect(() => {
    const f = () => setD(current)
    subs.add(f)
    return () => {
      subs.delete(f)
    }
  }, [])
  return d
}
