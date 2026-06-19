import { useEffect, useState, type ReactNode } from "react"
import { ActivityIcon, EraserIcon, RefreshCwIcon } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Progress } from "@/components/ui/progress"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
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
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { AtConsole } from "./AtConsole"
import { SimSwitcher } from "@/components/shell/SimSwitcher"
import { useSim } from "@/components/shell/SimProvider"
import { useStatus } from "@/hooks/queries"
import { useClearBuffer, usePatchSim, useStatusRefresh } from "@/hooks/mutations"
import { ago, carrierName, ceregLabel, displayMac, formatUptime, signalLabel, signalLevel, wifiSignalLevel } from "@/lib/format"
import { errorToast, successToast } from "@/lib/toast"
import { cn } from "@/lib/utils"

/** 连通性单元：状态点 + 标签 + 在线/离线 + 实时跳动的年龄。
 *  后端只给「响应时刻」的快照 baseAgeS，这里按数据拉取时刻 since 每秒递增。 */
function ConnCell({
  on,
  label,
  baseAgeS,
  since,
  now,
}: {
  on?: boolean
  label: string
  baseAgeS?: number
  since: number
  now: number
}) {
  // now 由父组件 StatusBody 的单个定时器下发,避免每个 ConnCell 各起 setInterval。
  const ageS =
    baseAgeS == null || baseAgeS < 0 ? -1 : Math.floor(baseAgeS + Math.max(0, (now - since) / 1000))
  return (
    <div className="flex items-center gap-2">
      <span className={cn("size-2 rounded-full transition-colors", on ? "bg-online" : "bg-muted-foreground/40")} />
      <span className="text-sm">{label}</span>
      <span className="text-xs text-muted-foreground">{on ? "在线" : "离线"}</span>
      <span className="ml-auto text-xs text-muted-foreground tabular-nums">{ago(ageS)}</span>
    </div>
  )
}

/** meta 单元：小标签 + 值。 */
function Stat({
  label,
  value,
}: {
  label: string
  value?: ReactNode
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-sm">{value ?? "—"}</span>
    </div>
  )
}

function StatusBody({ simId, multiple }: { simId: string; multiple: boolean }) {
  const { data: s, isLoading, dataUpdatedAt } = useStatus(simId)
  // 连通性年龄每秒递增:两个 ConnCell 共享单个定时器(而非各自 setInterval)。
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])
  const clear = useClearBuffer()
  const refresh = useStatusRefresh()
  const patchSim = usePatchSim()
  const [clearOpen, setClearOpen] = useState(false)

  const device = s?.device
  const modem = device?.modem
  const buffer = device?.buffer
  const counters = device?.counters
  const hub = s?.hub
  const [simName, setSimName] = useState(hub?.sim_name ?? "")
  useEffect(() => {
    setSimName(hub?.sim_name ?? "")
  }, [hub?.sim_name])
  const level = signalLevel(modem?.csq_dbm ?? -999)
  const bufferPct =
    buffer?.capacity && buffer.capacity > 0
      ? Math.min(100, Math.round(((buffer.count ?? 0) / buffer.capacity) * 100))
      : 0
  const bufferCount = buffer?.count ?? 0

  const doRefresh = () =>
    refresh.mutate(simId, {
      onSuccess: () => successToast("已向设备拉取最新状态"),
      onError: (e) => errorToast(e, "强制拉取失败"),
    })
  const doClear = () =>
    clear.mutate(simId, {
      onSuccess: (r) => successToast(`设备缓冲已排空，共 ${r.deleted} 条`),
      onError: (e) => errorToast(e, "设备缓冲排空失败"),
    })
  const doSaveName = () =>
    patchSim.mutate(
      { sim_id: simId, name: simName },
      {
        onSuccess: () => successToast("卡片备注已保存"),
        onError: (e) => errorToast(e, "卡片备注保存失败"),
      },
    )

  return (
    <>
      <div className="flex flex-col gap-4">
        {/* 多卡时顶部切换卡片（刷新交给下拉整页 / SSE / 兜底轮询） */}
        {multiple && (
          <div className="flex flex-wrap items-center gap-2">
            <SimSwitcher />
          </div>
        )}

        {/* 卡片和设备：实时摘要 + 身份/无线/遥测 */}
        <Card>
          <CardHeader>
            <CardTitle>卡片和设备</CardTitle>
            <CardAction>
              <Badge variant={s?.overall_online ? "success" : "secondary"}>
                {s?.overall_online ? "在线" : "离线"}
              </Badge>
            </CardAction>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            {isLoading ? (
              <Skeleton className="h-48 w-full" />
            ) : (
              <>
                {hub?.device_busy && (
                  <p className="text-sm text-muted-foreground">设备忙：{hub.device_busy}</p>
                )}
                {/* 双平面连通性 */}
                <div className="grid grid-cols-2 gap-3">
                  <ConnCell
                    on={s?.heartbeat_online}
                    label="心跳"
                    baseAgeS={s?.device_status_age_s}
                    since={dataUpdatedAt}
                    now={now}
                  />
                  <ConnCell
                    on={s?.data_plane_online}
                    label="数据面"
                    baseAgeS={hub?.last_poll_ago_s}
                    since={dataUpdatedAt}
                    now={now}
                  />
                </div>

                {/* 卡片备注 */}
                <div className="flex items-center gap-2">
                  <Input
                    value={simName}
                    onChange={(e) => setSimName(e.target.value)}
                    className="min-w-32 flex-1"
                    placeholder="备注名"
                  />
                  <Button
                    variant="outline"
                    onClick={() => simName !== (hub?.sim_name ?? "") && doSaveName()}
                    disabled={simName === (hub?.sim_name ?? "") || patchSim.isPending}
                  >
                    保存
                  </Button>
                </div>

                <Separator />

                {/* 卡片：身份 + 蜂窝网络 */}
                <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
                  <Stat label="本机号码" value={modem?.msisdn} />
                  <Stat label="运营商" value={carrierName(modem?.operator)} />
                  <Stat label="注册状态" value={ceregLabel(modem?.cereg)} />
                  <Stat
                    label="信号"
                    value={
                      modem?.csq_dbm != null
                        ? `${modem.csq_dbm} dBm（${signalLabel(level)}）`
                        : "—"
                    }
                  />
                  <Stat label="IMSI 尾号" value={modem?.imsi_tail} />
                  <Stat label="ICCID 尾号" value={modem?.iccid_tail} />
                </div>

                <Separator />

                {/* 设备：硬件 + WiFi + 运行 */}
                <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
                  <Stat label="设备 MAC" value={hub?.device_mac ? displayMac(hub.device_mac) : "—"} />
                  <Stat label="模组型号" value={modem?.model} />
                  <Stat label="固件" value={device?.fw} />
                  <Stat label="运行时长" value={formatUptime(device?.uptime_s)} />
                  <Stat label="WiFi SSID" value={device?.wifi_ssid} />
                  <Stat
                    label="WiFi 信号"
                    value={
                      device?.wifi_rssi != null
                        ? `${device.wifi_rssi} dBm（${signalLabel(wifiSignalLevel(device.wifi_rssi))}）`
                        : "—"
                    }
                  />
                  <Stat
                    label="收/发计数"
                    value={
                      <>
                        收 {counters?.rx_total ?? 0}
                        <span className="text-muted-foreground"> / </span>
                        发 {counters?.tx_total ?? 0}
                      </>
                    }
                  />
                  <Stat
                    label="可用堆"
                    value={device?.free_heap != null ? `${Math.round(device.free_heap / 1024)} KB` : "—"}
                  />
                </div>
              </>
            )}
          </CardContent>
          <CardFooter>
            <Button
              variant="outline"
              className="w-full"
              onClick={doRefresh}
              disabled={refresh.isPending}
            >
              <RefreshCwIcon data-icon="inline-start" className={cn(refresh.isPending && "animate-spin")} />
              {refresh.isPending ? "拉取中…" : "强制拉取"}
            </Button>
          </CardFooter>
        </Card>

        {/* Hub 同步 */}
        <Card>
          <CardHeader>
            <CardTitle>Hub 同步</CardTitle>
            <CardDescription>设备缓冲保留近 50 条作刷机/丢库兜底，默认不自动排空。</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            {isLoading ? (
              <Skeleton className="h-24 w-full" />
            ) : (
              <>
                <div className="flex flex-col gap-2">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">设备缓冲</span>
                    <span>
                      {buffer?.count ?? 0} / {buffer?.capacity ?? 0}
                    </span>
                  </div>
                  <Progress value={bufferPct} />
                  {(buffer?.dropped_total ?? 0) > 0 && (
                    <p className="text-xs text-muted-foreground">
                      设备缓冲溢出 {buffer?.dropped_total} 次（较早消息已从设备侧移出，Hub 已保存的仍在）
                    </p>
                  )}
                </div>
                <Separator />
                <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
                  <Stat label="本地存储" value={hub?.stored_total} />
                  <Stat label="游标" value={hub?.cursor} />
                  <Stat label="上次拉取" value={ago(hub?.last_poll_ago_s ?? -1)} />
                  <Stat label="上次 Hook" value={ago(hub?.last_hook_ago_s ?? -1)} />
                  <Stat
                    label="轮询间隔"
                    value={hub?.poll_interval_s != null ? `${hub.poll_interval_s}s` : "—"}
                  />
                </div>
              </>
            )}
          </CardContent>
          <CardFooter>
            <Button
              variant="destructive"
              className="w-full"
              onClick={() => setClearOpen(true)}
              disabled={bufferCount === 0 || clear.isPending}
            >
              <EraserIcon data-icon="inline-start" />
              排空缓冲
            </Button>
          </CardFooter>
        </Card>
      </div>

      {/* 排空设备缓冲确认（内容走 Portal，兄弟节点） */}
      <AlertDialog open={clearOpen} onOpenChange={setClearOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>排空设备缓冲？</AlertDialogTitle>
            <AlertDialogDescription>
              将删除设备本地缓冲中已同步到 Hub 的 {bufferCount} 条消息（Hub 库不受影响）。未同步的消息会保留，可用作刷机前释放设备空间。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={doClear} disabled={clear.isPending}>
              {clear.isPending ? <Spinner data-icon="inline-start" /> : <EraserIcon data-icon="inline-start" />}
              {clear.isPending ? "排空中…" : "排空"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

export function StatusView() {
  const { currentSimId, multiple } = useSim()

  if (!currentSimId) {
    return (
      <Empty>
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <ActivityIcon />
          </EmptyMedia>
          <EmptyTitle>没有启用的卡片</EmptyTitle>
          <EmptyDescription>请在设置页启用至少一张 SIM 卡后再查看状态。</EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <StatusBody simId={currentSimId} multiple={multiple} />
      <AtConsole simId={currentSimId} />
    </div>
  )
}
