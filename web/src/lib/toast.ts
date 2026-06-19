import { toast } from "sonner"

function singleLine(value: unknown): string {
  const text = value instanceof Error ? value.message : String(value ?? "")
  return text.replace(/\s+/g, " ").trim()
}

export function successToast(message: string) {
  toast.success(singleLine(message))
}

export function errorToast(error: unknown, fallback: string) {
  toast.error(singleLine(error) || singleLine(fallback))
}

/** 新短信到达提醒(顶部 sonner,多条按默认堆叠)。只做"有短信来"的轻提醒,详情看列表。 */
export function smsToast() {
  toast("收到新短信")
}
