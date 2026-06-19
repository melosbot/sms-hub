import { Fragment, useState } from "react"
import { RotateCcwIcon, SendIcon, Trash2Icon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
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
import { SimBadge } from "@/components/common/SimBadge"
import { useSim } from "@/components/shell/SimProvider"
import { useOutbound } from "@/hooks/queries"
import { useDeleteOutbound, useSend } from "@/hooks/mutations"
import { outboundStatus, relativeTime } from "@/lib/format"
import { errorToast, successToast } from "@/lib/toast"
import type { Outbound } from "@/lib/types"

export function OutboundTable() {
  const { sims } = useSim()
  const { data, isLoading } = useOutbound("all")
  const del = useDeleteOutbound()
  const send = useSend()
  const [deleteId, setDeleteId] = useState<number | null>(null)
  const rows = data?.outbound ?? []
  const simName = (id: string) => sims.find((s) => s.sim_id === id)?.name || "未命名卡"

  const resend = (o: Outbound) => {
    send.mutate(
      { sim_id: o.sim_id, to: o.to_phone, text: o.text },
      {
        onSuccess: (r) => successToast(`短信已重新加入发送队列，约 ${r.parts} 段`),
        onError: (e) => errorToast(e, "短信重发失败"),
      },
    )
  }

  const confirmDelete = () => {
    if (deleteId == null) return
    const id = deleteId
    del.mutate(id, {
      onSuccess: () => successToast("发件记录已删除"),
      onError: (e) => errorToast(e, "发件记录删除失败"),
    })
    setDeleteId(null)
  }

  return (
    <>
      <Card className="gap-0 py-0">
        <CardHeader className="pt-4">
          <CardTitle>发件记录</CardTitle>
        </CardHeader>
        <CardContent className="px-0">
          {isLoading ? (
            <div className="flex flex-col gap-3 px-4 py-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-20 w-full" />
              ))}
            </div>
          ) : rows.length === 0 ? (
            <Empty>
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <SendIcon />
                </EmptyMedia>
                <EmptyTitle>暂无发件记录</EmptyTitle>
                <EmptyDescription>发送的短信会在这里显示状态。</EmptyDescription>
              </EmptyHeader>
            </Empty>
          ) : (
            <div>
              {rows.map((o, i) => {
                const st = outboundStatus(o.status)
                const canResend = o.status === "failed" || o.status === "give_up"
                return (
                  <Fragment key={o.id}>
                    {i > 0 && (
                      <div className="px-4">
                        <Separator />
                      </div>
                    )}
                    <div className="flex flex-col gap-1.5 px-4 py-2.5">
                      {/* 1. 号码 + 时间 */}
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{o.to_phone}</span>
                        <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                          {relativeTime(o.created_at)}
                        </span>
                      </div>
                      {/* 2. 内容预览 */}
                      <p className="line-clamp-2 text-sm text-muted-foreground">{o.text}</p>
                      {/* 3. 卡片 / 状态 / 元数据（左）+ 操作（右） */}
                      <div className="flex items-center gap-2">
                        <SimBadge name={simName(o.sim_id)} />
                        <Badge variant={st.variant}>{st.label}</Badge>
                        {o.last_error ? (
                          <span className="min-w-0 flex-1 truncate text-xs text-destructive">
                            {o.last_error}
                          </span>
                        ) : (
                          // attempts = 总尝试次数（首次成功=1）；重试次数 = attempts - 1，仅 >1 时显示
                          <span className="text-xs text-muted-foreground">
                            {o.parts} 段{o.attempts > 1 ? ` · 重试 ${o.attempts - 1}` : ""}
                          </span>
                        )}
                        <div className="ml-auto flex shrink-0 items-center gap-1">
                          {canResend && (
                            <Button
                              variant="ghost"
                              size="icon"
                              aria-label="重发"
                              disabled={send.isPending}
                              onClick={() => resend(o)}
                            >
                              <RotateCcwIcon className={cn(send.isPending && "animate-spin")} />
                            </Button>
                          )}
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label="删除记录"
                            onClick={() => setDeleteId(o.id)}
                          >
                            <Trash2Icon />
                          </Button>
                        </div>
                      </div>
                    </div>
                  </Fragment>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* 删除确认 */}
      <AlertDialog open={deleteId !== null} onOpenChange={(o) => !o && setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除这条发件记录？</AlertDialogTitle>
            <AlertDialogDescription>将从发件记录中移除。</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={confirmDelete}>确认删除</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
