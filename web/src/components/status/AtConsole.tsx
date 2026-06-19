import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { useAt } from "@/hooks/mutations"
import { errorToast } from "@/lib/toast"
import type { AtResponse } from "@/lib/types"

const PRESETS = [
  { cmd: "AT+CSQ", label: "CSQ" },
  { cmd: "AT+CESQ", label: "CESQ" },
  { cmd: "AT+CNUM", label: "号码" },
  { cmd: "AT+CIMI", label: "IMSI" },
  { cmd: "AT+ICCID", label: "ICCID" },
  { cmd: "AT+CGATT?", label: "网络附着" },
]

export function AtConsole({ simId }: { simId: string }) {
  const at = useAt()
  const [cmd, setCmd] = useState("AT+CSQ")
  const [resp, setResp] = useState<AtResponse | null>(null)

  const run = (c: string = cmd) => {
    if (!c.trim()) return
    setCmd(c)
    at.mutate(
      { sim_id: simId, cmd: c },
      {
        onSuccess: (r) => {
          setResp(r)
          if (!r.ok) errorToast(r.response, "AT 命令执行失败")
        },
        onError: (e) => errorToast(e, "AT 命令执行失败"),
      },
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>AT 控制台</CardTitle>
        <CardDescription>对当前卡片的承载设备发起 AT 命令（设备忙时返回 409）。</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="grid grid-cols-4 gap-2">
          {PRESETS.map((p) => (
            <Button key={p.cmd} variant="outline" className="w-full" onClick={() => run(p.cmd)}>
              {p.label}
            </Button>
          ))}
        </div>
        <div className="flex gap-2">
          <Input
            value={cmd}
            onChange={(e) => setCmd(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run()}
            placeholder="AT 命令"
            className="min-w-0 flex-1"
          />
          <Button onClick={() => run()} disabled={at.isPending || !cmd.trim()}>
            {at.isPending && <Spinner data-icon="inline-start" />}
            {at.isPending ? "执行中…" : "执行"}
          </Button>
        </div>
        {resp && (
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-control bg-muted p-3 text-xs">
            {resp.response || "(无输出)"}
          </pre>
        )}
      </CardContent>
    </Card>
  )
}
