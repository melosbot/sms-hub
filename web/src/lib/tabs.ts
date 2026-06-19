import { Activity, Inbox, Send, Settings, type LucideIcon } from "lucide-react"

export type TabKey = "inbox" | "send" | "status" | "settings"

export interface TabDef {
  key: TabKey
  label: string
  icon: LucideIcon
}

export const TABS: TabDef[] = [
  { key: "inbox", label: "收件", icon: Inbox },
  { key: "send", label: "发送", icon: Send },
  { key: "status", label: "状态", icon: Activity },
  { key: "settings", label: "设置", icon: Settings },
]

export const DEFAULT_TAB: TabKey = "inbox"

export function parseTab(value: string | null): TabKey {
  return TABS.some((t) => t.key === value) ? (value as TabKey) : DEFAULT_TAB
}

export const NAV_EVENT = "smshub:nav"

/** 切换标签：写 ?tab= 并广播事件（AppShell 监听后同步 state）。使用 pushState 以支持浏览器后退。 */
export function navigate(tab: TabKey): void {
  const u = new URL(window.location.href)
  u.searchParams.set("tab", tab)
  window.history.pushState(null, "", u)
  window.dispatchEvent(new Event(NAV_EVENT))
}

