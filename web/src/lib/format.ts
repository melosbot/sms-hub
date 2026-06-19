// 展示层格式化：相对时间、MAC 展示、信号格、短信分段估算。

/** "2026-06-16 12:00:00"(本地时间字符串) → 相对时间。 */
export function relativeTime(dateStr: string): string {
  if (!dateStr) return ""
  const d = new Date(dateStr.replace(" ", "T"))
  const diff = (Date.now() - d.getTime()) / 1000
  if (Number.isNaN(diff)) return dateStr
  if (diff < 60) return "刚刚"
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`
  return dateStr.slice(5, 16)
}

/** 绝对时间短格式 "MM-DD HH:mm"。 */
export function shortTime(dateStr: string): string {
  if (!dateStr) return ""
  return dateStr.length >= 16 ? dateStr.slice(5, 16) : dateStr
}

/** 秒数 → "12s / 5m / 3h / 2d"，负值表示未知。 */
export function ago(s: number): string {
  if (s == null || s < 0) return "—"
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  return `${Math.floor(s / 86400)}d`
}

/** 规范化 mac(小写无分隔) → "AA:BB:CC:DD:EE:FF"（展示统一大写）。 */
export function displayMac(raw: string): string {
  if (!raw) return ""
  const upper = raw.toUpperCase()
  const m = upper.replace(/[^A-F0-9]/g, "")
  return m.length === 12 ? m.match(/.{2}/g)!.join(":") : upper
}

/** csq_dbm → 0..4 信号格。 */
export function signalLevel(dbm: number): number {
  if (dbm == null || Number.isNaN(dbm)) return 0
  if (dbm >= -70) return 4
  if (dbm >= -85) return 3
  if (dbm >= -100) return 2
  if (dbm >= -110) return 1
  return 0
}

export function signalLabel(level: number): string {
  return ["无信号", "较差", "一般", "较好", "很好"][level] ?? "—"
}

/** wifi_rssi(负 dBm) → 0..4 信号格。WiFi 阈值比蜂窝更高（同档信号 dBm 更大）。 */
export function wifiSignalLevel(rssi: number): number {
  if (rssi == null || Number.isNaN(rssi)) return 0
  if (rssi >= -50) return 4
  if (rssi >= -65) return 3
  if (rssi >= -75) return 2
  if (rssi >= -80) return 1
  return 0
}

/** 秒数 → 紧凑运行时长："3d2h" / "5h12m" / "12m30s" / "45s"；负或空 → "—"。 */
export function formatUptime(s?: number): string {
  if (s == null || s < 0 || !Number.isFinite(s)) return "—"
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = Math.floor(s % 60)
  if (d > 0) return `${d}d${h}h`
  if (h > 0) return `${h}h${m}m`
  if (m > 0) return `${m}m${sec}s`
  return `${sec}s`
}

/** CREG 网络注册状态码 → 中文（0 未注册 / 1 已注册 / 2 搜索中 / 3 被拒绝 / 5 漫游 …）。 */
export function ceregLabel(c?: number): string {
  switch (c) {
    case 0:
      return "未注册"
    case 1:
      return "已注册"
    case 2:
      return "搜索中"
    case 3:
      return "被拒绝"
    case 4:
      return "未知"
    case 5:
      return "漫游"
    case 6:
      return "仅短信"
    case 7:
      return "仅数据"
    default:
      return c != null ? `状态 ${c}` : "—"
  }
}

const GSM_RE = /^[\x20-\x7e]*$/ // 近似：纯 ASCII 可见字符按 GSM 7-bit 计

/** 估算短信分段：GSM(160/153) 或 UCS-2(70/67)。 */
export function estimateParts(text: string): { parts: number; perSeg: number; chars: number; ucs2: boolean } {
  const chars = text.length
  const ucs2 = !GSM_RE.test(text)
  if (chars === 0) return { parts: 0, perSeg: 0, chars, ucs2 }
  const single = ucs2 ? 70 : 160
  const multi = ucs2 ? 67 : 153
  const perSeg = chars > single ? multi : single
  return { parts: Math.max(1, Math.ceil(chars / perSeg)), perSeg, chars, ucs2 }
}

/** 发件状态 → 中文 + Badge 变体。 */
export function outboundStatus(s: string): { label: string; variant: "default" | "secondary" | "destructive" | "outline" | "success" | "warning" } {
  switch (s) {
    case "sent":
      return { label: "已发送", variant: "success" }
    case "pending":
    case "queued":
      return { label: "待发送", variant: "warning" }
    case "retry":
      return { label: "重试中", variant: "warning" }
    case "failed":
      return { label: "失败", variant: "destructive" }
    case "give_up":
      return { label: "已放弃", variant: "destructive" }
    default:
      return { label: s || "未知", variant: "outline" }
  }
}

/** 通知任务状态 → 中文。 */
export function notifyStatus(s: string): string {
  switch (s) {
    case "ok":
    case "sent":
      return "已送达"
    case "pending":
      return "待发送"
    case "retry":
      return "重试中"
    case "give_up":
      return "已放弃"
    default:
      return s || "—"
  }
}

/** 列表投递汇总(消息的 notify_jobs 聚合)→ 中文 + Badge 变体;无投递动作返回 null。 */
export function deliveryStatus(
  s?: string,
): { label: string; variant: "success" | "warning" | "destructive" } | null {
  switch (s) {
    case "delivered":
      return { label: "已投递", variant: "success" }
    case "pending":
      return { label: "投递中", variant: "warning" }
    case "failed":
      return { label: "投递失败", variant: "destructive" }
    default:
      return null
  }
}

/** 复制文本：剪贴板 API → execCommand 回退 → prompt 手动复制兜底。 */
export async function copyText(text: string): Promise<boolean> {
  // 1) 剪贴板 API（安全上下文：localhost / https）
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      /* 落到回退 */
    }
  }
  // 2) execCommand 回退（弹窗焦点捕获 / 非安全上下文下可能失败）
  try {
    const ta = document.createElement("textarea")
    ta.value = text
    ta.setAttribute("readonly", "")
    ta.style.position = "fixed"
    ta.style.top = "0"
    ta.style.left = "0"
    ta.style.fontSize = "16px"
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    ta.setSelectionRange(0, text.length)
    let ok = false
    try {
      ok = document.execCommand("copy")
    } catch {
      ok = false
    }
    ta.remove()
    if (ok) return true
  } catch {
    /* 落到兜底 */
  }
  // 3) 最后兜底：弹窗让用户手动长按复制（最可靠，不受焦点捕获/权限影响）
  try {
    window.prompt("自动复制失败，请长按选择并复制：", text)
  } catch {
    /* ignore */
  }
  return false
}
