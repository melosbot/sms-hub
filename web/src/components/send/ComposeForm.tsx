import { useEffect, useMemo, useState, type FormEvent } from "react"
import { CheckIcon, ChevronDownIcon, SendIcon } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { useConfig, useContacts, useDevices } from "@/hooks/queries"
import { useSend } from "@/hooks/mutations"
import { useSim } from "@/components/shell/SimProvider"
import { setComposeDraft, useComposeDraft, type ComposeDraft } from "@/lib/compose"
import { estimateParts } from "@/lib/format"
import { errorToast, successToast } from "@/lib/toast"
import type { SimView } from "@/lib/types"

function simLabel(s: SimView): string {
  return s.name || s.msisdn || (s.imsi_tail ? `IMSI …${s.imsi_tail}` : s.sim_id)
}

export function ComposeForm() {
  const { enabledSims } = useSim()
  const { data: contacts } = useContacts()
  const { data: cfg } = useConfig()
  const { data: devicesData } = useDevices()
  const send = useSend()
  const draft = useComposeDraft()

  const defaultSimId = cfg?.default_send_sim_id ?? ""
  const [simId, setSimId] = useState("")
  const [to, setTo] = useState("")
  const [text, setText] = useState("")
  const [confirmOpen, setConfirmOpen] = useState(false)

  // 从收件箱「回复」预填：渲染期校正 state（React 推荐模式，避免 effect 内 setState），
  // 仅覆盖 draft 提供的字段；effect 只负责消费后清空外部 store（副作用，非本地 setter）
  const [appliedDraft, setAppliedDraft] = useState<ComposeDraft | null>(null)
  if (draft && draft !== appliedDraft) {
    setAppliedDraft(draft)
    if (draft.to) setTo(draft.to)
    if (draft.text) setText(draft.text)
    if (draft.sim_id) setSimId(draft.sim_id)
  }
  useEffect(() => {
    if (draft) setComposeDraft(null)
  }, [draft])

  // 默认卡置顶（作为主要卡），其余按原序
  const orderedSims = useMemo(() => {
    if (!defaultSimId) return enabledSims
    const def = enabledSims.find((s) => s.sim_id === defaultSimId)
    if (!def) return enabledSims
    return [def, ...enabledSims.filter((s) => s.sim_id !== defaultSimId)]
  }, [enabledSims, defaultSimId])

  // 有效发送卡：用户选择失效时回退默认卡 / 首张（派生，避免 effect 内 setState）
  const activeSimId = useMemo(() => {
    const valid = (id: string) => !!id && enabledSims.some((s) => s.sim_id === id)
    if (valid(simId)) return simId
    if (valid(defaultSimId)) return defaultSimId
    return enabledSims[0]?.sim_id ?? ""
  }, [simId, enabledSims, defaultSimId])

  const est = estimateParts(text)
  const devices = devicesData?.devices ?? []
  const current = enabledSims.find((s) => s.sim_id === activeSimId)
  // 承载设备是否在线：无卡 / 承载设备离线时禁用发送，避免假成功后空等重试
  const bearerOnline = current
    ? devices.some((d) => d.mac === current.current_device_mac && d.online)
    : false
  const hasCards = enabledSims.length > 0
  const canSend =
    hasCards && bearerOnline && !!to.trim() && !!text.trim() && !send.isPending
  const sendLabel = !hasCards ? "无可用 SIM 卡" : !bearerOnline ? "设备离线" : "发送短信"

  // 提交先弹确认框（不直接发送）
  const openConfirm = (e: FormEvent) => {
    e.preventDefault()
    if (!canSend) return
    setConfirmOpen(true)
  }

  const doSend = () => {
    if (!activeSimId || !to.trim() || !text.trim()) return
    send.mutate(
      { sim_id: activeSimId, to: to.trim(), text: text.trim() },
      {
        onSuccess: (r) => {
          successToast(`短信已加入发送队列，约 ${r.parts} 段`)
          setText("")
        },
        onError: (e) => errorToast(e, "短信发送失败"),
      },
    )
  }

  return (
    <Card>
      <form onSubmit={openConfirm} className="contents">
        <CardHeader>
          <CardTitle>发送短信</CardTitle>
        </CardHeader>
        <CardContent>
          <FieldGroup className="gap-4">
            <Field>
              <FieldLabel htmlFor="to">收件人</FieldLabel>
              <Input
                id="to"
                list="contacts-dl"
                inputMode="tel"
                value={to}
                onChange={(e) => setTo(e.target.value)}
                placeholder="手机号"
              />
              <datalist id="contacts-dl">
                {contacts?.contacts.map((c) => (
                  <option key={c.phone} value={c.phone}>
                    {c.alias}
                  </option>
                ))}
              </datalist>
            </Field>

            <Field>
              <div className="flex items-center justify-between gap-2">
                <FieldLabel htmlFor="text">内容</FieldLabel>
                <span className="text-xs text-muted-foreground">
                  {est.chars} 字 · 约 {est.parts} 条{est.ucs2 ? " · 含非 GSM 字符" : ""}
                </span>
              </div>
              <Textarea
                id="text"
                value={text}
                onChange={(e) => setText(e.target.value)}
                rows={4}
                placeholder="短信内容"
              />
            </Field>
          </FieldGroup>
        </CardContent>
        <CardFooter className="flex flex-col gap-2">
          {enabledSims.length > 1 && (
            // 多卡：通道选择独立成行（DropdownMenu 走 Portal 浮顶层，不被 Card 裁剪）
            <div className="flex w-full items-center justify-between gap-2">
              <span className="text-sm text-muted-foreground">发送通道</span>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline">
                    {current ? simLabel(current) : "选择卡片"}
                    <ChevronDownIcon data-icon="inline-end" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="min-w-52">
                  <DropdownMenuGroup>
                    {orderedSims.map((s) => (
                      <DropdownMenuItem key={s.sim_id} onSelect={() => setSimId(s.sim_id)}>
                        <span className="flex-1 truncate">{simLabel(s)}</span>
                        {s.sim_id === defaultSimId && (
                          <Badge variant="secondary">默认</Badge>
                        )}
                        {activeSimId === s.sim_id && <CheckIcon />}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuGroup>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          )}
          <Button type="submit" disabled={!canSend} className="w-full">
            <SendIcon data-icon="inline-start" />
            {sendLabel}
          </Button>
        </CardFooter>
      </form>

      {/* 发送前确认（内容走 Portal，不在 form 内，不会触发提交） */}
      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent className="sm:max-w-md">
          <AlertDialogHeader>
            <AlertDialogTitle>确认发送？</AlertDialogTitle>
            <AlertDialogDescription>
              将经「{current ? simLabel(current) : "该卡"}」发送至 {to.trim() || "—"}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="line-clamp-4 whitespace-pre-wrap break-words rounded-control bg-muted p-3 text-sm">
            {text.trim()}
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={doSend} disabled={send.isPending}>
              {send.isPending ? <Spinner data-icon="inline-start" /> : <SendIcon data-icon="inline-start" />}
              {send.isPending ? "发送中…" : "确认发送"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  )
}
