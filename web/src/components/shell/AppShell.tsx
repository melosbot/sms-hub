import { useEffect, useRef, useState } from "react"
import { Sidebar } from "./Sidebar"
import { BottomNav } from "./BottomNav"
import { Header } from "./Header"
import { SimProvider } from "./SimProvider"
import { InboxView } from "@/components/inbox/InboxView"
import { SendView } from "@/components/send/SendView"
import { StatusView } from "@/components/status/StatusView"
import { SettingsView } from "@/components/settings/SettingsView"
import { useFallbackPolling, useSSE } from "@/lib/sse"
import { NAV_EVENT, navigate, parseTab, type TabKey } from "@/lib/tabs"
import { smsToast } from "@/lib/toast"
import type { SSEEvent } from "@/lib/types"

function readTab(): TabKey {
  return parseTab(new URLSearchParams(window.location.search).get("tab"))
}

export function AppShell() {
  const [tab, setTab] = useState<TabKey>(readTab)

  useEffect(() => {
    const sync = () => setTab(readTab())
    window.addEventListener(NAV_EVENT, sync)
    window.addEventListener("popstate", sync)
    return () => {
      window.removeEventListener(NAV_EVENT, sync)
      window.removeEventListener("popstate", sync)
    }
  }, [])

  // 收到 new_messages 弹顶部 sonner;seenNotifIdRef 防重连重复弹。
  const seenNotifIdRef = useRef<number | null>(null)
  const connected = useSSE((e: SSEEvent) => {
    if (e.type !== "new_messages" || !e.latest) return
    const m = e.latest
    if (seenNotifIdRef.current === m.id) return
    seenNotifIdRef.current = m.id
    smsToast()
  })
  useFallbackPolling(connected)

  return (
    <SimProvider>
      <div className="min-h-svh bg-background text-foreground">
        <BottomNav active={tab} onChange={navigate} />
        <Sidebar active={tab} onChange={navigate} />

        <div className="md:pl-sidebar">
          <Header />
          <main className="mx-auto w-full max-w-app px-4 py-4 pb-24 md:pb-8">
            <div
              key={tab}
              className="animate-in fade-in-0 duration-200"
            >
              {tab === "inbox" && <InboxView />}
              {tab === "send" && <SendView />}
              {tab === "status" && <StatusView />}
              {tab === "settings" && <SettingsView />}
            </div>
          </main>
        </div>
      </div>
    </SimProvider>
  )
}
