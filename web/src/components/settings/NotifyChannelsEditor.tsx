import { useState } from "react"
import {
  PlusIcon,
  SendIcon,
  Trash2Icon,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
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
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Switch } from "@/components/ui/switch"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { Spinner } from "@/components/ui/spinner"
import { useConfig } from "@/hooks/queries"
import { useNotifyTest, useSaveConfig } from "@/hooks/mutations"
import { errorToast, successToast } from "@/lib/toast"
import type { ChannelType, NotifyChannel } from "@/lib/types"

const TEMPLATE_VARIABLES = "支持 {sender}、{code}、{text} 等变量。"

/** 配置完整性 → 徽标(避免开了开关但配置不全)。enabled 由开关表达,徽标只看配置。 */
function configStatus(ch: NotifyChannel): {
  label: string
  variant: "success" | "warning" | "secondary"
} {
  const c = ch.config
  const complete: Record<ChannelType, boolean> = {
    telegram: (!!c.bot_token || !!c.bot_token_set) && !!c.chat_id,
    sms_forward: !!c.to,
    webhook_json: !!c.url,
    webhook_get: !!c.url,
    bark: !!c.url,
    dingtalk: !!c.url,
    feishu: !!c.url,
    pushplus: !!c.token,
    serverchan: !!c.sendkey || !!c.url,
    gotify: !!c.url && !!c.token,
  }
  return complete[ch.type]
    ? { label: "已配置", variant: "success" }
    : { label: "配置不全", variant: "warning" }
}

/** 新增渠道选择器:10 种。Bark/Server酱/PushPlus/Gotify 为专门后端 type。 */
interface Preset {
  type: ChannelType
  name: string
  template?: string
}
const CHANNEL_PRESETS: Preset[] = [
  { type: "telegram", name: "Telegram" },
  { type: "sms_forward", name: "短信转发" },
  { type: "webhook_json", name: "POST JSON" },
  { type: "webhook_get", name: "GET 请求" },
  { type: "bark", name: "Bark" },
  { type: "pushplus", name: "PushPlus" },
  { type: "serverchan", name: "Server酱" },
  { type: "dingtalk", name: "钉钉机器人" },
  { type: "feishu", name: "飞书机器人" },
  { type: "gotify", name: "Gotify" },
]

function templatePlaceholder(t: ChannelType): string {
  return t === "webhook_json" ? '{"sender":"{sender}","text":"{text}"}' : "{sender}: {text}"
}

export function NotifyChannelsEditor() {
  const { data: cfg } = useConfig()
  const editorKey = cfg
    ? JSON.stringify([cfg.tg_manage_enabled, cfg.notify_channels])
    : "loading"

  return (
    <NotifyChannelsForm
      key={editorKey}
      initialChannels={cfg?.notify_channels ?? []}
      initialTgManage={cfg?.tg_manage_enabled ?? false}
    />
  )
}

function NotifyChannelsForm({
  initialChannels,
  initialTgManage,
}: {
  initialChannels: NotifyChannel[]
  initialTgManage: boolean
}) {
  const save = useSaveConfig()
  const test = useNotifyTest()
  const [channels, setChannels] = useState<NotifyChannel[]>(() =>
    structuredClone(initialChannels),
  )
  const [tgManage, setTgManage] = useState(initialTgManage)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [addOpen, setAddOpen] = useState(false)

  const editing = channels.find((c) => c.id === editingId) ?? null
  const deletingChannel = channels.find((c) => c.id === deletingId) ?? null

  const updateChannel = (id: string, patch: Partial<NotifyChannel>) =>
    setChannels((prev) => prev.map((c) => (c.id === id ? { ...c, ...patch } : c)))
  const updateCfg = (id: string, patch: Record<string, unknown>) =>
    setChannels((prev) =>
      prev.map((c) => (c.id === id ? { ...c, config: { ...c.config, ...patch } } : c)),
    )

  const saveAll = () => {
    save.mutate(
      { notify_channels: channels, tg_manage_enabled: tgManage },
      {
        onSuccess: () => successToast("通知设置已保存"),
        onError: (e) => errorToast(e, "通知设置保存失败"),
      },
    )
  }

  const testChannel = (ch: NotifyChannel) =>
    test.mutate(ch.id, {
      onSuccess: (res) => {
        if (res.ok) successToast(`${ch.name} 测试通知已发送`)
        else errorToast(res.error, "测试通知发送失败")
      },
      onError: (e) => errorToast(e, "测试通知发送失败"),
    })

  const addPreset = (p: Preset) => {
    const id = `${p.type}-${Math.random().toString(36).slice(2, 6)}`
    const ch: NotifyChannel = {
      id,
      type: p.type,
      name: p.name,
      enabled: false,
      config: p.template ? { template: p.template } : {},
    }
    setChannels((prev) => [...prev, ch])
    setAddOpen(false)
    setEditingId(id)
  }

  const removeChannel = (id: string) => {
    setChannels((prev) => prev.filter((c) => c.id !== id))
    setEditingId(null)
    setDeletingId(null)
  }

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>通知渠道</CardTitle>
          <CardAction>
            <Button type="button" variant="outline" onClick={() => setAddOpen(true)}>
              <PlusIcon data-icon="inline-start" />
              添加
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {channels.length === 0 ? (
            <p className="text-sm text-muted-foreground">暂无通知渠道,点击「添加」新建。</p>
          ) : (
            <div className="flex flex-col gap-2">
              {channels.map((ch) => {
                const status = configStatus(ch)
                return (
                  <div
                    key={ch.id}
                    className="flex items-center justify-between gap-3 rounded-control bg-muted/40 px-4 py-3"
                  >
                    <div className="flex min-w-0 flex-col gap-1">
                      <span className="truncate text-sm font-medium">{ch.name}</span>
                      <span className="truncate text-xs text-muted-foreground">
                        {status.label} · {ch.enabled ? "已开启" : "未开启"}
                      </span>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      <Button variant="ghost" onClick={() => setEditingId(ch.id)}>
                        编辑
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`删除 ${ch.name}`}
                        onClick={() => setDeletingId(ch.id)}
                      >
                        <Trash2Icon />
                      </Button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>

      <AlertDialog open={deletingId != null} onOpenChange={(o) => !o && setDeletingId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除「{deletingChannel?.name}」渠道？</AlertDialogTitle>
            <AlertDialogDescription>将从通知渠道中移除，保存后生效。</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => deletingId && removeChannel(deletingId)}
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <ChannelDialog
        channel={editing}
        tgManage={tgManage}
        onTgManage={setTgManage}
        onCfg={(patch) => editing && updateCfg(editing.id, patch)}
        onChannel={(patch) => editing && updateChannel(editing.id, patch)}
        onClose={() => setEditingId(null)}
        onTest={() => editing && testChannel(editing)}
        onSave={saveAll}
        testing={test.isPending && test.variables === editing?.id}
      />

      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>添加通知渠道</DialogTitle>
            <DialogDescription>选择渠道类型。</DialogDescription>
          </DialogHeader>
          <div className="grid grid-cols-2 gap-2">
            {CHANNEL_PRESETS.map((p) => (
              <Button key={p.name} type="button" variant="outline" onClick={() => addPreset(p)}>
                {p.name}
              </Button>
            ))}
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}

function ChannelDialog({
  channel,
  tgManage,
  onTgManage,
  onCfg,
  onChannel,
  onClose,
  onTest,
  onSave,
  testing,
}: {
  channel: NotifyChannel | null
  tgManage: boolean
  onTgManage: (v: boolean) => void
  onCfg: (patch: Record<string, unknown>) => void
  onChannel: (patch: Partial<NotifyChannel>) => void
  onClose: () => void
  onTest: () => void
  onSave: () => void
  testing: boolean
}) {
  const ch = channel
  return (
    <Dialog open={ch != null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90svh] gap-0 overflow-y-auto sm:max-w-lg">
        {ch && (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                {ch.name}
                <Badge variant={configStatus(ch).variant}>{configStatus(ch).label}</Badge>
              </DialogTitle>
              <DialogDescription className="sr-only">通知渠道配置</DialogDescription>
            </DialogHeader>

            <div className="flex flex-col gap-4 p-1">
              <Field orientation="horizontal">
                <FieldLabel htmlFor="ch-enabled">启用渠道</FieldLabel>
                <Switch
                  id="ch-enabled"
                  checked={ch.enabled}
                  onCheckedChange={(v) => onChannel({ enabled: v })}
                />
              </Field>

              <ChannelFields ch={ch} onCfg={onCfg} />

              {ch.type === "telegram" && (
                <>
                  <Separator />
                  <Field orientation="horizontal">
                    <FieldContent>
                      <FieldLabel htmlFor="tg-manage">启用 Telegram 管理命令</FieldLabel>
                      <FieldDescription>启用后可用 /status、/sms、/history。</FieldDescription>
                    </FieldContent>
                    <Switch id="tg-manage" checked={tgManage} onCheckedChange={onTgManage} />
                  </Field>
                </>
              )}

              {ch.type !== "webhook_get" && (
                <Field>
                  <FieldLabel htmlFor="ch-template">消息模板</FieldLabel>
                  <Textarea
                    id="ch-template"
                    rows={2}
                    value={ch.config.template ?? ""}
                    onChange={(e) => onCfg({ template: e.target.value })}
                    placeholder={templatePlaceholder(ch.type)}
                  />
                  <FieldDescription>留空用默认格式。{TEMPLATE_VARIABLES}</FieldDescription>
                </Field>
              )}
            </div>

            <DialogFooter className="flex-row gap-2">
              <Button type="button" variant="outline" className="flex-1" onClick={onTest} disabled={testing}>
                {testing ? <Spinner data-icon="inline-start" /> : <SendIcon data-icon="inline-start" />}
                {testing ? "发送中…" : "测试"}
              </Button>
              <Button type="button" className="flex-1" onClick={onSave}>保存</Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}

function ChannelFields({
  ch,
  onCfg,
}: {
  ch: NotifyChannel
  onCfg: (patch: Record<string, unknown>) => void
}) {
  const cfg = ch.config
  switch (ch.type) {
    case "telegram":
      return (
        <FieldGroup>
          <Field>
            <FieldLabel htmlFor="tg-token">Bot Token</FieldLabel>
            <Input
              id="tg-token"
              type="password"
              value={cfg.bot_token ?? ""}
              onChange={(e) => onCfg({ bot_token: e.target.value })}
              placeholder={cfg.bot_token_set ? "已设置，留空可保留" : "输入 Bot Token"}
            />
          </Field>
          <Field>
            <FieldLabel htmlFor="tg-chat">Chat ID</FieldLabel>
            <Input
              id="tg-chat"
              value={cfg.chat_id ?? ""}
              onChange={(e) => onCfg({ chat_id: e.target.value })}
              placeholder="例如 123456789"
            />
          </Field>
          <Field>
            <FieldLabel htmlFor="tg-api">Telegram API 地址</FieldLabel>
            <Input
              id="tg-api"
              value={cfg.api_base ?? ""}
              onChange={(e) => onCfg({ api_base: e.target.value })}
              placeholder="https://api.telegram.org"
            />
            <FieldDescription>仅使用反向代理时修改。</FieldDescription>
          </Field>
        </FieldGroup>
      )
    case "sms_forward":
      return (
        <Field>
          <FieldLabel htmlFor="sf-to">转发目标手机号</FieldLabel>
          <Input
            id="sf-to"
            value={cfg.to ?? ""}
            onChange={(e) => onCfg({ to: e.target.value })}
            inputMode="tel"
            placeholder="例如 13800138000"
          />
          <FieldDescription>不会转发该号码发来的短信。</FieldDescription>
        </Field>
      )
    case "webhook_json":
    case "webhook_get":
      return (
        <Field>
          <FieldLabel htmlFor="wh-url">
            {ch.type === "webhook_get" ? "请求 URL" : "Webhook URL"}
          </FieldLabel>
          <Input
            id="wh-url"
            value={cfg.url ?? ""}
            onChange={(e) => onCfg({ url: e.target.value })}
            placeholder="https://..."
          />
          {ch.type === "webhook_get" && <FieldDescription>{TEMPLATE_VARIABLES}</FieldDescription>}
        </Field>
      )
    case "dingtalk":
    case "feishu":
      return (
        <FieldGroup>
          <Field>
            <FieldLabel htmlFor="bot-url">Webhook URL</FieldLabel>
            <Input
              id="bot-url"
              value={cfg.url ?? ""}
              onChange={(e) => onCfg({ url: e.target.value })}
              placeholder="https://oapi.dingtalk.com/robot/send?access_token=..."
            />
          </Field>
          <Field>
            <FieldLabel htmlFor="bot-secret">加签 Secret</FieldLabel>
            <Input
              id="bot-secret"
              type="password"
              value={cfg.secret ?? ""}
              onChange={(e) => onCfg({ secret: e.target.value })}
              placeholder={cfg.secret_set ? "已设置，留空可保留" : "可选，启用加签验证"}
            />
            <FieldDescription>留空则不加签（兼容未开加签的机器人）。</FieldDescription>
          </Field>
        </FieldGroup>
      )
    case "bark":
      return (
        <Field>
          <FieldLabel htmlFor="bark-url">Bark 服务器 URL</FieldLabel>
          <Input
            id="bark-url"
            value={cfg.url ?? ""}
            onChange={(e) => onCfg({ url: e.target.value })}
            placeholder="https://api.day.app/你的key"
          />
          <FieldDescription>含设备 Key 的 Bark 推送地址。</FieldDescription>
        </Field>
      )
    case "pushplus":
      return (
        <FieldGroup>
          <Field>
            <FieldLabel htmlFor="pp-token">Token</FieldLabel>
            <Input
              id="pp-token"
              value={cfg.token ?? ""}
              onChange={(e) => onCfg({ token: e.target.value })}
              placeholder="PushPlus token"
            />
          </Field>
          <Field>
            <FieldLabel htmlFor="pp-channel">推送渠道</FieldLabel>
            <Input
              id="pp-channel"
              value={cfg.channel ?? ""}
              onChange={(e) => onCfg({ channel: e.target.value })}
              placeholder="留空用 wechat"
            />
            <FieldDescription>可选：wechat / extension / app。</FieldDescription>
          </Field>
        </FieldGroup>
      )
    case "serverchan":
      return (
        <Field>
          <FieldLabel htmlFor="sc-sendkey">SendKey</FieldLabel>
          <Input
            id="sc-sendkey"
            value={cfg.sendkey ?? ""}
            onChange={(e) => onCfg({ sendkey: e.target.value })}
            placeholder="Server酱 SendKey"
          />
          <FieldDescription>URL 留空时用官方接口 + SendKey。</FieldDescription>
        </Field>
      )
    case "gotify":
      return (
        <FieldGroup>
          <Field>
            <FieldLabel htmlFor="gf-url">Gotify 服务器 URL</FieldLabel>
            <Input
              id="gf-url"
              value={cfg.url ?? ""}
              onChange={(e) => onCfg({ url: e.target.value })}
              placeholder="https://gotify.example.com"
            />
          </Field>
          <Field>
            <FieldLabel htmlFor="gf-token">应用 Token</FieldLabel>
            <Input
              id="gf-token"
              value={cfg.token ?? ""}
              onChange={(e) => onCfg({ token: e.target.value })}
              placeholder="Gotify app token"
            />
          </Field>
        </FieldGroup>
      )
  }
}
