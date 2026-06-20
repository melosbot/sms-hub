import { useEffect, useState } from "react"
import { PlusIcon, Trash2Icon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Field, FieldLabel } from "@/components/ui/field"
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
import { useContacts } from "@/hooks/queries"
import { useDeleteContact, useSaveContact } from "@/hooks/mutations"
import { errorToast, successToast } from "@/lib/toast"
import type { Contact } from "@/lib/types"

export function ContactsCard() {
  const { data } = useContacts()
  const contacts = data?.contacts ?? []
  const save = useSaveContact()
  const del = useDeleteContact()

  const [editing, setEditing] = useState<Contact | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [deleting, setDeleting] = useState<Contact | null>(null)

  const openNew = () => {
    setEditing(null)
    setDialogOpen(true)
  }
  const openEdit = (c: Contact) => {
    setEditing(c)
    setDialogOpen(true)
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>号码备注</CardTitle>
        <CardDescription>为来源号码设置易读名称。</CardDescription>
        <CardAction>
          <Button type="button" variant="outline" onClick={openNew}>
            <PlusIcon data-icon="inline-start" />
            添加
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        {contacts.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂无号码备注,点击「添加」新建。</p>
        ) : (
          <div className="flex flex-col gap-2">
            {contacts.map((c) => (
              <div
                key={c.phone}
                className="flex items-center justify-between gap-3 rounded-control bg-muted/40 px-4 py-3"
              >
                <div className="flex min-w-0 flex-col gap-0.5">
                  <span className="truncate text-sm font-medium">{c.phone}</span>
                  <span className="truncate text-sm text-muted-foreground">
                    {c.alias || "（未命名）"}
                  </span>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button variant="ghost" onClick={() => openEdit(c)}>
                    编辑
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`删除 ${c.phone} 的备注`}
                    onClick={() => setDeleting(c)}
                  >
                    <Trash2Icon />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>

      <ContactDialog
        open={dialogOpen}
        editing={editing}
        onOpenChange={(o) => {
          setDialogOpen(o)
          if (!o) setEditing(null)
        }}
        pending={save.isPending}
        onSubmit={(phone, alias) =>
          save.mutate(
            { phone, alias },
            {
              onSuccess: () => {
                setDialogOpen(false)
                setEditing(null)
                successToast(editing ? "号码备注已更新" : "号码备注已添加")
              },
              onError: (e) => errorToast(e, "号码备注保存失败"),
            },
          )
        }
      />

      <AlertDialog open={deleting != null} onOpenChange={(o) => !o && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除「{deleting?.phone}」的备注?</AlertDialogTitle>
            <AlertDialogDescription>
              将清除该号码的备注名,后续短信按原号码显示。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() =>
                deleting &&
                del.mutate(deleting.phone, {
                  onSuccess: () => {
                    setDeleting(null)
                    successToast("号码备注已删除")
                  },
                  onError: (e) => errorToast(e, "号码备注删除失败"),
                })
              }
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  )
}

function ContactDialog({
  open,
  editing,
  onOpenChange,
  onSubmit,
  pending,
}: {
  open: boolean
  editing: Contact | null
  onOpenChange: (o: boolean) => void
  onSubmit: (phone: string, alias: string) => void
  pending: boolean
}) {
  const isEdit = editing != null
  const [phone, setPhone] = useState("")
  const [alias, setAlias] = useState("")

  useEffect(() => {
    if (open) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- 打开编辑对话框时初始化表单
      setPhone(editing?.phone ?? "")
      setAlias(editing?.alias ?? "")
    }
  }, [open, editing])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{isEdit ? "编辑备注" : "添加备注"}</DialogTitle>
          <DialogDescription>设置号码的显示名称。</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-4 p-1">
          <Field>
            <FieldLabel htmlFor="c-phone">手机号码</FieldLabel>
            <Input
              id="c-phone"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="例如 10086"
              inputMode="tel"
              disabled={isEdit}
            />
          </Field>
          <Field>
            <FieldLabel htmlFor="c-alias">备注名</FieldLabel>
            <Input
              id="c-alias"
              value={alias}
              onChange={(e) => setAlias(e.target.value)}
              placeholder="例如 中国移动"
            />
          </Field>
        </div>
        <DialogFooter className="flex-row gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button disabled={!phone.trim() || pending} onClick={() => onSubmit(phone.trim(), alias.trim())}>
            {pending && <Spinner data-icon="inline-start" />}
            {pending ? "保存中…" : "保存"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
