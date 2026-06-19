// 浏览器→Hub API 客户端。注入 Bearer token；非 2xx 抛 ApiError(detail)；401 清 token 并广播事件。
import type {
  AtResponse,
  ClearBufferResponse,
  Config,
  Contact,
  DevicesResponse,
  MessageDetail,
  MessagesResponse,
  NotifyTestResponse,
  OkResponse,
  OutboundResponse,
  RefreshResponse,
  SendResponse,
  StatusResponse,
} from "./types"

const TOKEN_KEY = "smshub.token"
export const UNAUTH_EVENT = "smshub:unauth"

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || ""
}
export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = "ApiError"
    this.status = status
  }
}

function authHeaders(): Record<string, string> {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { ...authHeaders() }
  let payload: BodyInit | undefined
  if (body !== undefined) {
    headers["Content-Type"] = "application/json"
    payload = JSON.stringify(body)
  }
  let res: Response
  try {
    res = await fetch(path, { method, headers, body: payload })
  } catch (e) {
    throw new ApiError(0, `网络错误：${e instanceof Error ? e.message : "无法连接"}`)
  }
  if (res.status === 401) {
    clearToken()
    window.dispatchEvent(new Event(UNAUTH_EVENT))
  }
  if (!res.ok) {
    let detail = `请求失败 (${res.status})`
    try {
      const ct = res.headers.get("content-type") || ""
      if (ct.includes("application/json")) {
        const j = (await res.json()) as { detail?: string }
        detail = j.detail || detail
      } else {
        const t = await res.text()
        if (t) detail = t
      }
    } catch {
      /* keep default */
    }
    throw new ApiError(res.status, detail)
  }
  const ct = res.headers.get("content-type") || ""
  if (ct.includes("application/json")) {
    return (await res.json()) as T
  }
  return (await res.text()) as unknown as T
}

function qs(params: Record<string, string | number | undefined | null>): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") sp.set(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ""
}

const enc = encodeURIComponent

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string, body?: unknown) => request<T>("DELETE", path, body),

  // ── 具体端点 ────────────────────────────────────────────────────
  login: (user: string, password: string) =>
    request<{ token: string }>("POST", "/api/login", { user, password }).then((r) => r.token),

  devices: () => api.get<DevicesResponse>("/api/devices"),
  config: () => api.get<Config>("/api/config"),
  saveConfig: (body: Partial<Config>) => api.post<OkResponse>("/api/config", body),
  notifyTest: (channel: string) =>
    api.post<NotifyTestResponse>("/api/notify/test", { channel }),

  status: (sim_id: string) => api.get<StatusResponse>(`/api/status${qs({ sim_id })}`),
  refreshStatus: (sim_id: string) => api.post<RefreshResponse>("/api/status/refresh", { sim_id }),
  clearBuffer: (sim_id: string) => api.post<ClearBufferResponse>("/api/buffer/clear", { sim_id }),
  at: (sim_id: string, cmd: string, timeout_ms = 3000) =>
    api.post<AtResponse>("/api/at", { sim_id, cmd, timeout_ms }),

  messages: (p: { sim_id?: string; q?: string; limit?: number; offset?: number }) =>
    api.get<MessagesResponse>(`/api/messages${qs(p)}`),
  messageDetail: (id: number) => api.get<MessageDetail>(`/api/messages/${id}`),
  deleteMessage: (id: number) => api.del<OkResponse>(`/api/messages/${id}`),
  deleteMessages: (ids: number[]) => api.del<OkResponse>("/api/messages", { ids }),

  send: (sim_id: string, to: string, text: string) =>
    api.post<SendResponse>("/api/send", { sim_id, to, text }),
  outbound: (sim_id: string) => api.get<OutboundResponse>(`/api/outbound${qs({ sim_id })}`),
  deleteOutbound: (id: number) => api.del<OkResponse>(`/api/outbound/${id}`),

  contacts: () => api.get<{ contacts: Contact[] }>("/api/contacts"),
  saveContact: (phone: string, alias: string) =>
    api.put<OkResponse>(`/api/contacts/${enc(phone)}`, { alias }),
  deleteContact: (phone: string) => api.del<OkResponse>(`/api/contacts/${enc(phone)}`),

  patchDevice: (mac: string, body: { name?: string; enabled?: boolean }) =>
    api.patch<OkResponse>(`/api/devices/${mac}`, body),
  patchSim: (sim_id: string, body: { name?: string; enabled?: boolean }) =>
    api.patch<OkResponse>(`/api/sims/${sim_id}`, body),
}

// 导出下载（需鉴权 header，无法用 <a> 直接下载）
export async function downloadExport(sim_id: string, fmt: "csv" | "json", q = ""): Promise<void> {
  const path = `/api/messages/export${qs({ sim_id, fmt, limit: 20000, q })}`
  const res = await fetch(path, { headers: authHeaders() })
  if (!res.ok) throw new ApiError(res.status, "导出失败")
  const blob = await res.blob()
  const cd = res.headers.get("content-disposition") || ""
  const m = cd.match(/filename="?([^"]+)"?$/)
  const name = m?.[1] || `sms-export.${fmt}`
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
