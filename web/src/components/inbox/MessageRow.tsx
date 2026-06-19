import { useState } from "react"
import { CheckIcon, CopyIcon, ShieldBanIcon, Trash2Icon } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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
import type { Message } from "@/lib/types"
import { copyText, deliveryStatus, relativeTime } from "@/lib/format"
import { successToast } from "@/lib/toast"
import { cn } from "@/lib/utils"
import { SimBadge } from "@/components/common/SimBadge"

interface Props {
  msg: Message
  simName: string
  selectMode: boolean
  selected: boolean
  onOpen: () => void
  onToggleSelect: () => void
  onDelete: () => void
}

export function MessageRow({ msg, simName, selectMode, selected, onOpen, onToggleSelect, onDelete }: Props) {
  const [confirmOpen, setConfirmOpen] = useState(false)
  const alias = msg.sender_alias?.trim()
  const brand = msg.brand?.trim()
  const label = alias || brand || msg.sender
  const identified = !!(alias || brand)
  const delivery = deliveryStatus(msg.delivery)

  const handleClick = () => {
    if (selectMode) onToggleSelect()
    else onOpen()
  }
  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault()
      handleClick()
    }
  }

  // 拦截冒泡：点击/按键只作用于控件本身，不触发行打开或选择
  const swallow = (e: React.SyntheticEvent) => e.stopPropagation()

  // 点击验证码徽标复制
  const copyCode = async () => {
    if (!msg.code) return
    if (await copyText(msg.code)) successToast("验证码已复制")
  }
  const onCodeClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    void copyCode()
  }
  const onCodeKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault()
      e.stopPropagation()
      void copyCode()
    }
  }

  // AlertDialog 作为行的兄弟节点（而非子节点）：否则 Portal 内容的点击会沿
  // React 树冒泡到行 div[role=button] 的 onClick，误开 MessageDetail。
  return (
    <>
      <div
        role="button"
        tabIndex={0}
        onClick={handleClick}
        onKeyDown={handleKey}
        className="flex cursor-pointer gap-3 px-4 py-2.5 text-left transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-inset focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        {selectMode && (
          <span
            className={cn(
              "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border",
              selected ? "border-primary bg-primary text-primary-foreground" : "border-input",
            )}
          >
            {selected && <CheckIcon className="size-3.5" />}
          </span>
        )}

        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          {/* 1. 发件人 + 时间 */}
          <div className="flex items-center gap-2">
            <span className="truncate font-medium">{label}</span>
            {identified && (
              <span className="truncate text-xs text-muted-foreground">{msg.sender}</span>
            )}
            {msg.blocked ? (
              <ShieldBanIcon className="size-3.5 shrink-0 text-muted-foreground" />
            ) : null}
            <span className="ml-auto shrink-0 text-xs text-muted-foreground">
              {relativeTime(msg.received_at)}
            </span>
          </div>

          {/* 2. 内容预览（彩信的 text 已由 Hub 置为下载地址，统一显示） */}
          <p className="line-clamp-2 break-all text-sm text-muted-foreground">{msg.text}</p>

          {/* 3. 卡片来源 + 验证码（点击复制）+ 删除（右） */}
          <div className="flex items-center gap-2">
            <SimBadge name={simName} />
            {msg.content_type === "mms" && <Badge variant="secondary">彩信</Badge>}
            {msg.code && (
              <Badge
                variant="code"
                role="button"
                tabIndex={0}
                className="cursor-pointer select-none"
                aria-label={`复制验证码 ${msg.code}`}
                title="点击复制"
                onClick={onCodeClick}
                onKeyDown={onCodeKey}
              >
                {msg.code}
                <CopyIcon />
              </Badge>
            )}
            {delivery && <Badge variant={delivery.variant}>{delivery.label}</Badge>}
            <Button
              variant="ghost"
              size="icon"
              className="ml-auto"
              aria-label="删除"
              onClick={(e) => {
                swallow(e)
                setConfirmOpen(true)
              }}
              onKeyDown={swallow}
            >
              <Trash2Icon />
            </Button>
          </div>
        </div>
      </div>

      {/* 删除确认（行的兄弟节点；内容走 Portal） */}
      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除这条短信？</AlertDialogTitle>
            <AlertDialogDescription>将从本地永久删除并写入设备墓碑。</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={onDelete}>确认删除</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
