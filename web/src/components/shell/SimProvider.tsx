import { createContext, useContext, useMemo, type ReactNode } from "react"
import { useDevices } from "@/hooks/queries"
import { useLocalStorage } from "@/hooks/useLocalStorage"
import type { SimView } from "@/lib/types"

interface SimCtxValue {
  sims: SimView[]
  enabledSims: SimView[]
  currentSimId: string
  setCurrentSimId: (id: string) => void
  multiple: boolean
  current: SimView | undefined
}

const SimCtx = createContext<SimCtxValue | null>(null)

/**
 * 当前卡片上下文（docs/guide.md §5.2）。
 * - 单张启用卡：自动选定，隐藏切换器。
 * - 多张启用卡：读 localStorage「smshub.sim」；失效时回退第一张。
 * 当前卡只存前端，不写 Hub 全局，多标签页互不影响。
 */
export function SimProvider({ children }: { children: ReactNode }) {
  const { data } = useDevices()
  const sims = useMemo(() => data?.sims ?? [], [data?.sims])
  const enabledSims = useMemo(() => sims.filter((s) => s.enabled), [sims])
  const multiple = enabledSims.length > 1
  const [stored, setStored] = useLocalStorage("smshub.sim", "")

  const currentSimId = useMemo(() => {
    if (enabledSims.length === 0) return ""
    if (enabledSims.length === 1) return enabledSims[0].sim_id
    if (stored && enabledSims.some((s) => s.sim_id === stored)) return stored
    return enabledSims[0].sim_id
  }, [enabledSims, stored])

  const current = [...enabledSims, ...sims].find((s) => s.sim_id === currentSimId)

  const value: SimCtxValue = {
    sims,
    enabledSims,
    currentSimId,
    setCurrentSimId: setStored,
    multiple,
    current,
  }
  return <SimCtx.Provider value={value}>{children}</SimCtx.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useSim(): SimCtxValue {
  const v = useContext(SimCtx)
  if (!v) throw new Error("useSim 必须在 SimProvider 内使用")
  return v
}
