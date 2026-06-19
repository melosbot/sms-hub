import { useState, type FormEvent } from "react"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useConfig, useDevices } from "@/hooks/queries"
import { useSaveConfig } from "@/hooks/mutations"
import { errorToast, successToast } from "@/lib/toast"
import type { SimView } from "@/lib/types"

interface FormState {
  admin_phone: string
  blacklist: string
  poll_interval: number
  keepalive_interval_days: number
  keepalive_ping_host: string
  tombstone_keep_days: number
  message_keep_days: number
  default_send_sim_id: string
}

const DEFAULT_FORM: FormState = {
  admin_phone: "",
  blacklist: "",
  poll_interval: 60,
  keepalive_interval_days: 0,
  keepalive_ping_host: "119.29.29.29",
  tombstone_keep_days: 30,
  message_keep_days: 0,
  default_send_sim_id: "",
}

function NumberField({
  label,
  value,
  onChange,
  description,
  min,
}: {
  label: string
  value: number
  onChange: (v: number) => void
  description?: string
  min?: number
}) {
  return (
    <Field>
      <FieldLabel>{label}</FieldLabel>
      <Input
        type="number"
        min={min}
        value={Number.isFinite(value) ? value : 0}
        onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value))}
      />
      {description && <FieldDescription>{description}</FieldDescription>}
    </Field>
  )
}

export function GlobalConfigForm() {
  const { data: cfg } = useConfig()
  const { data: dev } = useDevices()
  const initialForm = cfg
    ? {
        admin_phone: cfg.admin_phone,
        blacklist: cfg.blacklist,
        poll_interval: cfg.poll_interval,
        keepalive_interval_days: cfg.keepalive_interval_days,
        keepalive_ping_host: cfg.keepalive_ping_host,
        tombstone_keep_days: cfg.tombstone_keep_days,
        message_keep_days: cfg.message_keep_days,
        default_send_sim_id: cfg.default_send_sim_id,
      }
    : DEFAULT_FORM
  const formKey = JSON.stringify(initialForm)

  return <GlobalConfigEditor key={formKey} initialForm={initialForm} sims={dev?.sims ?? []} />
}

function GlobalConfigEditor({
  initialForm,
  sims,
}: {
  initialForm: FormState
  sims: SimView[]
}) {
  const save = useSaveConfig()
  const [f, setF] = useState<FormState>(() => structuredClone(initialForm))

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setF((prev) => ({ ...prev, [k]: v }))

  const submit = (e: FormEvent) => {
    e.preventDefault()
    save.mutate(f, {
      onSuccess: () => successToast("系统设置已保存"),
      onError: (e) => errorToast(e, "系统设置保存失败"),
    })
  }

  return (
    <Card>
      <form onSubmit={submit} className="contents">
        <CardHeader>
          <CardTitle>系统设置</CardTitle>
        </CardHeader>
        <CardContent>
          <FieldGroup className="gap-4">
            <Field>
              <FieldLabel>管理员手机号</FieldLabel>
              <Input
                value={f.admin_phone}
                onChange={(e) => set("admin_phone", e.target.value)}
                inputMode="tel"
                placeholder="例如 13800138000"
              />
              <FieldDescription>
                该号码不触发通知，并可发起短信代发。
              </FieldDescription>
            </Field>
            <Field>
              <FieldLabel>号码黑名单</FieldLabel>
              <Input
                value={f.blacklist}
                onChange={(e) => set("blacklist", e.target.value)}
                placeholder="例如 1069, 95533*"
              />
              <FieldDescription>
                英文逗号分隔；完整号码精确匹配，短号码和 * 规则按前缀匹配。
              </FieldDescription>
            </Field>

            <Field>
              <FieldLabel>默认发送卡片</FieldLabel>
              <Select
                value={f.default_send_sim_id}
                onValueChange={(v) => set("default_send_sim_id", v)}
              >
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="不指定" />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    <SelectItem value="">不指定</SelectItem>
                    {sims.map((s) => (
                      <SelectItem key={s.sim_id} value={s.sim_id}>
                        {s.name || `…${s.imsi_tail}` || s.sim_id}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
              <FieldDescription>用于管理员代发和 Telegram /sms 命令。</FieldDescription>
            </Field>

            <Accordion type="single" collapsible>
              <AccordionItem value="advanced">
                <AccordionTrigger>高级设置</AccordionTrigger>
                <AccordionContent>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <NumberField
                      label="短信轮询间隔（秒）"
                      value={f.poll_interval}
                      min={5}
                      onChange={(v) => set("poll_interval", v)}
                      description="Webhook 异常时的兜底间隔，最短 5 秒。"
                    />
                    <NumberField
                      label="保号任务间隔（天）"
                      value={f.keepalive_interval_days}
                      min={0}
                      onChange={(v) => set("keepalive_interval_days", v)}
                      description="0 表示关闭。"
                    />
                    <Field>
                      <FieldLabel>保号检测主机</FieldLabel>
                      <Input
                        value={f.keepalive_ping_host}
                        onChange={(e) => set("keepalive_ping_host", e.target.value)}
                        placeholder="例如 119.29.29.29"
                      />
                    </Field>
                    <NumberField
                      label="删除记录保留时间（天）"
                      value={f.tombstone_keep_days}
                      min={1}
                      onChange={(v) => set("tombstone_keep_days", v)}
                      description="防止已删除短信再次同步，最少 1 天。"
                    />
                    <NumberField
                      label="短信保留时间（天）"
                      value={f.message_keep_days}
                      min={0}
                      onChange={(v) => set("message_keep_days", v)}
                      description="0 表示永久保留。"
                    />
                  </div>
                </AccordionContent>
              </AccordionItem>
            </Accordion>
          </FieldGroup>
        </CardContent>
        <CardFooter>
          <Button type="submit" className="w-full" disabled={save.isPending}>
            {save.isPending && <Spinner data-icon="inline-start" />}
            {save.isPending ? "正在保存…" : "保存系统设置"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  )
}
