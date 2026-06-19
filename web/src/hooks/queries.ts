import { useQuery, keepPreviousData } from "@tanstack/react-query"
import { api } from "@/lib/api"

export interface MessagesParams {
  sim_id?: string
  q?: string
  limit?: number
  offset?: number
}

// 查询 key 工厂：SSE/轮询按前缀失效（invalidateQueries(["messages"]) 命中所有参数变体）。
export const qk = {
  devices: ["devices"] as const,
  config: ["config"] as const,
  status: (sim_id: string) => ["status", sim_id] as const,
  messages: (p: MessagesParams) => ["messages", p] as const,
  messageDetail: (id: number) => ["message", id] as const,
  outbound: (sim_id: string) => ["outbound", sim_id] as const,
  contacts: ["contacts"] as const,
}

const STALE = 30_000

export function useDevices() {
  return useQuery({ queryKey: qk.devices, queryFn: api.devices, staleTime: STALE })
}

export function useConfig() {
  return useQuery({ queryKey: qk.config, queryFn: api.config, staleTime: STALE })
}

export function useStatus(sim_id: string | undefined) {
  return useQuery({
    queryKey: qk.status(sim_id ?? ""),
    queryFn: () => api.status(sim_id!),
    enabled: !!sim_id,
    placeholderData: keepPreviousData,
    staleTime: STALE,
  })
}

export function useMessages(p: MessagesParams) {
  return useQuery({
    queryKey: qk.messages(p),
    queryFn: () => api.messages(p),
    placeholderData: keepPreviousData,
    staleTime: STALE,
  })
}

export function useMessageDetail(id: number | null) {
  return useQuery({
    queryKey: qk.messageDetail(id ?? 0),
    queryFn: () => api.messageDetail(id!),
    enabled: id != null,
  })
}

export function useOutbound(sim_id: string) {
  return useQuery({ queryKey: qk.outbound(sim_id), queryFn: () => api.outbound(sim_id), staleTime: STALE })
}

export function useContacts() {
  return useQuery({ queryKey: qk.contacts, queryFn: api.contacts, staleTime: STALE })
}
