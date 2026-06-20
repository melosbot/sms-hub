import { useEffect, useState } from "react"
import { CopyIcon, SendIcon, Trash2Icon } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
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
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { useMessageDetail } from "@/hooks/queries"
import { useDeleteMessage, useSend } from "@/hooks/mutations"
import { copyText, estimateParts, notifyStatus } from "@/lib/format"
import { errorToast, successToast } from "@/lib/toast"

function Meta({
  label,
  value,
}: {
  label: string
  value?: string | number | null
}) {
  if (value === undefined || value === null || value === "") return null
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="break-all text-sm">{value}</span>
    </div>
  )
}

export function MessageDetail({ id, onClose }: { id: number | null; onClose: () => void }) {
  const q = useMessageDetail(id)
  const del = useDeleteMessage()
  const send = useSend()
  const msg = q.data
  const [reply, setReply] = useState("")

  // 打开（另一条）短信时清空回复草稿（切换 id 时通过 key 重置）
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 切换短信时需要清空草稿
    if (id != null) setReply("")
  }, [id])

  const est = estimateParts(reply)

  const doDelete = () => {
    if (id == null) return
    del.mutate(id, {
      onSuccess: () => {
        successToast("短信已删除")
        onClose()
      },
      onError: (e) => errorToast(e, "短信删除失败"),
    })
  }

  // 直接经「接收卡片」回复原发件人
  const doReply = () => {
    if (!msg || !reply.trim()) return
    send.mutate(
      { sim_id: msg.sim_id, to: msg.sender, text: reply.trim() },
      {
        onSuccess: (r) => {
          successToast(`回复已加入发送队列，约 ${r.parts} 段`)
          setReply("")
        },
        onError: (e) => errorToast(e, "回复发送失败"),
      },
    )
  }

  const copyCode = async () => {
    if (!msg?.code) return
    if (await copyText(msg.code)) successToast("验证码已复制")
  }

  return (
    <Dialog open={id != null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent
        className="flex max-h-[90svh] flex-col gap-0 p-0 sm:max-w-lg"
        onOpenAutoFocus={(e) => e.preventDefault()}
      >
        <DialogHeader className="px-4 pt-4">
          <DialogTitle className="flex flex-wrap items-center gap-2">
            {msg ? msg.sender_alias || msg.brand || msg.sender : "加载中…"}
            {(msg?.sender_alias || msg?.brand) && (
              <span className="text-sm font-normal text-muted-foreground">{msg.sender}</span>
            )}
          </DialogTitle>
          <DialogDescription>{msg?.received_at ?? ""}</DialogDescription>
        </DialogHeader>

        {q.isLoading || !msg ? (
          <div className="flex flex-1 flex-col gap-2 overflow-y-auto p-4">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-4 w-2/3" />
          </div>
        ) : (
          <>
            <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-4 pt-2">
              <div className="whitespace-pre-wrap break-words rounded-control bg-muted p-3 text-sm">
                {msg.text}
              </div>

              <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                <Meta label="收件卡" value={msg.sim_name || msg.sim_imsi_tail || msg.sim_id} />
                <Meta label="验证码" value={msg.code || "N/A"} />
              </div>

              {msg.notify && msg.notify.length > 0 && (
                <div className="flex flex-col gap-1.5">
                  <span className="text-xs text-muted-foreground">推送记录</span>
                  {msg.notify.map((n, i) => (
                    <div
                      key={i}
                      className="flex items-center justify-between rounded-chip border px-2.5 py-1.5 text-sm"
                    >
                      <span>{n.channel}</span>
                      <span className="flex items-center gap-2 text-muted-foreground">
                        <span>{notifyStatus(n.status)}</span>
                        {n.attempts > 0 && <span className="text-xs">重试 {n.attempts} 次</span>}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {/* 直接回复：经接收该短信的卡片发回原发件人 */}
              <Separator />
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium">快捷回复</span>
                  <span className="text-xs text-muted-foreground">
                    {est.chars} 字 · 约 {est.parts} 条
                  </span>
                </div>
                <Textarea
                  value={reply}
                  onChange={(e) => setReply(e.target.value)}
                  rows={3}
                  placeholder="回复内容"
                />
              </div>
            </div>

            {/* 底部操作区,固定不随内容滚动 */}
            <div className="grid grid-cols-3 gap-2 border-t bg-muted/50 p-4">
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button variant="destructive" className="w-full">
                    <Trash2Icon data-icon="inline-start" />
                    删除
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>删除这条短信？</AlertDialogTitle>
                    <AlertDialogDescription>
                      将从本地永久删除并写入设备墓碑，防止旧缓存回流。设备侧删除为尽力而为。
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>取消</AlertDialogCancel>
                    <AlertDialogAction variant="destructive" onClick={doDelete}>确认删除</AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
              <Button variant="outline" className="w-full" onClick={copyCode} disabled={!msg.code}>
                <CopyIcon data-icon="inline-start" />
                复制验证码
              </Button>
              <Button className="w-full" onClick={doReply} disabled={!reply.trim() || send.isPending}>
                {send.isPending ? <Spinner data-icon="inline-start" /> : <SendIcon data-icon="inline-start" />}
                {send.isPending ? "发送中…" : "发送"}
              </Button>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
