import { Fragment, useEffect, useMemo, useState } from "react"
import { CheckCheckIcon, DownloadIcon, InboxIcon, ListChecksIcon, SearchIcon, ShieldCheckIcon, Trash2Icon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Card, CardContent } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
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
import { MessageRow } from "./MessageRow"
import { MessageDetail } from "./MessageDetail"
import { useDevices, useMessages } from "@/hooks/queries"
import { useDeleteMessages } from "@/hooks/mutations"
import { downloadExport } from "@/lib/api"
import { errorToast, successToast } from "@/lib/toast"

export function InboxView() {
  const { data: dev } = useDevices()
  const simNameById = useMemo(() => {
    const m: Record<string, string> = {}
    for (const s of dev?.sims ?? []) m[s.sim_id] = s.name
    return m
  }, [dev?.sims])

  const [qInput, setQInput] = useState("")
  const [q, setQ] = useState("")
  const [limit, setLimit] = useState(30)
  const [selectMode, setSelectMode] = useState(false)
  const [selected, setSelected] = useState<Set<number>>(() => new Set())
  const [detailId, setDetailId] = useState<number | null>(null)
  const [onlyCode, setOnlyCode] = useState(false)

  useEffect(() => {
    const id = setTimeout(() => setQ(qInput.trim()), 300)
    return () => clearTimeout(id)
  }, [qInput])

  const { data, isLoading, isFetching } = useMessages({ sim_id: "all", q, limit, offset: 0 })
  const msgs = useMemo(() => data?.messages ?? [], [data?.messages])
  const total = data?.total ?? 0
  const shown = useMemo(() => (onlyCode ? msgs.filter((m) => m.code) : msgs), [msgs, onlyCode])
  const delMany = useDeleteMessages()

  const toggle = (id: number) =>
    setSelected((prev) => {
      const n = new Set(prev)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })
  const exitSelect = () => {
    setSelectMode(false)
    setSelected(new Set())
  }
  const allSelected = shown.length > 0 && shown.every((m) => selected.has(m.id))
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(shown.map((m) => m.id)))

  const doExport = async (fmt: "csv" | "json") => {
    try {
      await downloadExport("all", fmt, q)
      successToast("短信导出已开始")
    } catch (e) {
      errorToast(e, "短信导出失败")
    }
  }

  const doBatchDelete = () => {
    delMany.mutate([...selected], {
      onSuccess: (r) => {
        const n = (r as { deleted?: number }).deleted ?? selected.size
        successToast(`短信已删除，共 ${n} 条`)
        exitSelect()
      },
      onError: (e) => errorToast(e, "短信删除失败"),
    })
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="relative">
        <SearchIcon className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={qInput}
          onChange={(e) => setQInput(e.target.value)}
          placeholder="搜索 发件人/内容/备注"
          className="pl-8"
        />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {selectMode ? (
          <>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="destructive" disabled={selected.size === 0}>
                  <Trash2Icon data-icon="inline-start" />
                  删除
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>删除选中的 {selected.size} 条短信？</AlertDialogTitle>
                  <AlertDialogDescription>将永久从本地删除并写入设备墓碑。</AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>取消</AlertDialogCancel>
                  <AlertDialogAction variant="destructive" onClick={doBatchDelete}>确认删除</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
            <Button variant="outline" onClick={toggleAll}>
              <CheckCheckIcon data-icon="inline-start" />
              {allSelected ? "取消全选" : "全选"}
            </Button>
            <Button variant="ghost" onClick={exitSelect}>
              取消
            </Button>
            <span className="ml-auto text-sm text-muted-foreground">已选 {selected.size}</span>
          </>
        ) : (
          <>
            <Button variant="outline" onClick={() => setSelectMode(true)}>
              <ListChecksIcon data-icon="inline-start" />
              选择
            </Button>
            <Button
              variant={onlyCode ? "secondary" : "outline"}
              onClick={() => setOnlyCode((v) => !v)}
            >
              <ShieldCheckIcon data-icon="inline-start" />
              验证
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline">
                  <DownloadIcon data-icon="inline-start" />
                  导出
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                <DropdownMenuGroup>
                  <DropdownMenuItem onClick={() => doExport("csv")}>导出 CSV</DropdownMenuItem>
                  <DropdownMenuItem onClick={() => doExport("json")}>导出 JSON</DropdownMenuItem>
                </DropdownMenuGroup>
              </DropdownMenuContent>
            </DropdownMenu>
            <span className="ml-auto text-xs text-muted-foreground">
              共 {onlyCode ? shown.length : total} 条{onlyCode ? " 验证码" : ""}
            </span>
          </>
        )}
      </div>

      {isLoading ? (
        <Card className="gap-0 py-0">
          <CardContent className="px-0">
            {Array.from({ length: 4 }).map((_, i) => (
              <Fragment key={i}>
                {i > 0 && <div className="px-4"><Separator /></div>}
                <div className="px-4 py-3">
                  <Skeleton className="h-20 w-full" />
                </div>
              </Fragment>
            ))}
          </CardContent>
        </Card>
      ) : msgs.length === 0 ? (
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <InboxIcon />
            </EmptyMedia>
            <EmptyTitle>暂无短信</EmptyTitle>
            <EmptyDescription>收到新短信时会通过实时推送自动刷新。</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <Card className="gap-0 py-0">
          <CardContent className="px-0">
            {shown.length === 0 ? (
              <p className="px-4 py-8 text-center text-sm text-muted-foreground">
                当前页无验证码短信
              </p>
            ) : (
              shown.map((m, i) => (
                <Fragment key={m.id}>
                  {i > 0 && <div className="px-4"><Separator /></div>}
                  <MessageRow
                    msg={m}
                    simName={simNameById[m.sim_id] ?? m.sim_name}
                    selectMode={selectMode}
                    selected={selected.has(m.id)}
                    onOpen={() => setDetailId(m.id)}
                    onToggleSelect={() => toggle(m.id)}
                    onDelete={() =>
                      delMany.mutate([m.id], {
                        onSuccess: () => successToast("短信已删除"),
                        onError: (e) => errorToast(e, "短信删除失败"),
                      })
                    }
                  />
                </Fragment>
              ))
            )}
          </CardContent>
        </Card>
      )}

      {msgs.length > 0 && msgs.length < total && (
        <Button variant="outline" className="w-full" onClick={() => setLimit((l) => Math.min(200, l + 30))} disabled={isFetching}>
          {isFetching ? "加载中…" : "加载更多"}
        </Button>
      )}

      <MessageDetail id={detailId} onClose={() => setDetailId(null)} />
    </div>
  )
}
