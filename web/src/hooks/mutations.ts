import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { Config } from "@/lib/types"

// 所有 mutation 在成功后失效相关查询；toast/错误处理交给调用方组件。

export function useSend() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (p: { sim_id: string; to: string; text: string }) => api.send(p.sim_id, p.to, p.text),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["outbound"] }),
  })
}

export function useDeleteMessage() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.deleteMessage(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["messages"] }),
  })
}

export function useDeleteMessages() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (ids: number[]) => api.deleteMessages(ids),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["messages"] }),
  })
}

export function usePatchDevice() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ mac, ...body }: { mac: string; name?: string; enabled?: boolean }) =>
      api.patchDevice(mac, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["devices"] })
      qc.invalidateQueries({ queryKey: ["status"] })
    },
  })
}

export function usePatchSim() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ sim_id, ...body }: { sim_id: string; name?: string; enabled?: boolean }) =>
      api.patchSim(sim_id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["devices"] })
      qc.invalidateQueries({ queryKey: ["status"] })
    },
  })
}

export function useSaveConfig() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Partial<Config>) => api.saveConfig(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["config"] })
      qc.invalidateQueries({ queryKey: ["devices"] })
    },
  })
}

export function useNotifyTest() {
  return useMutation({
    mutationFn: (channel: string) => api.notifyTest(channel),
  })
}

export function useAt() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (p: { sim_id: string; cmd: string; timeout_ms?: number }) =>
      api.at(p.sim_id, p.cmd, p.timeout_ms),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["status"] }),
  })
}

export function useStatusRefresh() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (sim_id: string) => api.refreshStatus(sim_id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["status"] })
      qc.invalidateQueries({ queryKey: ["devices"] })
    },
  })
}

export function useClearBuffer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (sim_id: string) => api.clearBuffer(sim_id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["status"] })
      qc.invalidateQueries({ queryKey: ["devices"] })
    },
  })
}

export function useSaveContact() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (p: { phone: string; alias: string }) => api.saveContact(p.phone, p.alias),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["contacts"] }),
  })
}

export function useDeleteContact() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (phone: string) => api.deleteContact(phone),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["contacts"] }),
  })
}

export function useDeleteOutbound() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.deleteOutbound(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["outbound"] }),
  })
}
