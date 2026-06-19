import { useEffect, useState } from "react"
import { AppShell } from "@/components/shell/AppShell"
import { Login } from "@/components/Login"
import { getToken, UNAUTH_EVENT } from "@/lib/api"

export default function App() {
  const [authed, setAuthed] = useState<boolean>(() => !!getToken())

  useEffect(() => {
    const onUnauth = () => setAuthed(false)
    window.addEventListener(UNAUTH_EVENT, onUnauth)
    return () => window.removeEventListener(UNAUTH_EVENT, onUnauth)
  }, [])

  if (!authed) return <Login onLoggedIn={() => setAuthed(true)} />
  return <AppShell />
}
