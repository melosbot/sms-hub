import { useCallback, useEffect, useState } from "react"

/** localStorage 持久化的 state（跨标签页 storage 事件同步）。 */
export function useLocalStorage(key: string, initial: string): [string, (v: string) => void] {
  const [value, setValue] = useState<string>(() => localStorage.getItem(key) ?? initial)

  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === key) setValue(e.newValue ?? initial)
    }
    window.addEventListener("storage", onStorage)
    return () => window.removeEventListener("storage", onStorage)
  }, [key, initial])

  const set = useCallback(
    (v: string) => {
      setValue(v)
      localStorage.setItem(key, v)
    },
    [key],
  )

  return [value, set]
}
