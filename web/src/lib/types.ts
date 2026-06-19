// 与 core/app/routes/* 实际响应结构对齐的类型定义。

/** 设备模组状态（设备心跳/状态快照里的 modem 块）。 */
export interface ModemInfo {
  ready?: boolean
  model?: string
  cereg?: number
  csq_dbm?: number
  operator?: string
  sim?: boolean
  imsi_tail?: string
  msisdn?: string
  iccid_tail?: string
  apn?: string
  flight_mode?: number
  pdp_active?: boolean
}

/** 设备本地短信缓冲。 */
export interface BufferInfo {
  count?: number
  capacity?: number
  latest_id?: number
  dropped_total?: number
}

/** 设备收发计数。 */
export interface DeviceCounters {
  rx_total?: number
  tx_total?: number
  webhook_fail_total?: number
}

/** 设备状态快照（心跳/boot/hello 携带的完整 body）。 */
export interface DeviceSnapshot {
  fw?: string
  uptime_s?: number
  free_heap?: number
  min_free_heap?: number
  wifi_rssi?: number
  wifi_ssid?: string
  reset_reason?: string
  last_error?: string
  heartbeat_interval_s?: number
  modem?: ModemInfo
  buffer?: BufferInfo
  counters?: DeviceCounters
}

export interface DeviceView {
  mac: string
  display_mac: string
  name: string
  enabled: boolean
  online: boolean
  heartbeat_online: boolean
  data_plane_online: boolean
  last_heartbeat_ago_s: number
  last_poll_ago_s: number
  last_hook_ago_s: number
  cursor: number
  busy: string
  buffer: BufferInfo
  modem: ModemInfo
  current_sim_id: string
}

export type IdentitySource = "imsi" | "temporary"
export type ChannelType =
  | "telegram"
  | "sms_forward"
  | "webhook_json"
  | "webhook_get"
  | "dingtalk"
  | "feishu"
  | "bark"
  | "pushplus"
  | "serverchan"
  | "gotify"

export interface SimView {
  sim_id: string
  name: string
  enabled: boolean
  current_device_mac: string
  identity_source: IdentitySource
  msisdn: string
  imsi_tail: string
  iccid_tail: string
  operator: string
}

export interface DevicesResponse {
  devices: DeviceView[]
  sims: SimView[]
}

export interface Message {
  id: number
  sim_id: string
  sim_name: string
  sim_imsi_tail?: string
  device_mac: string
  device_msg_id: number
  gw_epoch: number
  sender: string
  sender_alias?: string
  text: string
  scts?: string
  received_at: string
  code: string | null
  complete?: number
  truncated?: number
  blocked?: number
  created_at?: string
  brand?: string
  content_type: string
  mms_url?: string
  mms_size?: number
  delivery?: string
}

export interface MessagesResponse {
  total: number
  messages: Message[]
  readonly: boolean
}

export interface NotifyJob {
  channel: string
  status: string
  attempts: number
  last_error: string
  created_at?: string
}

export interface MessageDetail extends Message {
  notify: NotifyJob[]
}

export interface Outbound {
  id: number
  sim_id: string
  device_mac: string
  to_phone: string
  text: string
  device_msg_id: number | null
  status: string
  parts: number
  attempts: number
  next_attempt_ts: number
  source: string
  last_error: string
  created_at: string
}

export interface OutboundResponse {
  outbound: Outbound[]
}

export interface Contact {
  phone: string
  alias: string
  updated_at: string
}

export interface StatusHub {
  sim_id: string
  sim_name: string
  device_mac: string
  stored_total: number
  cursor: number
  last_poll_ago_s: number
  last_hook_ago_s: number
  poll_interval_s: number
  device_busy: string
}

export interface StatusResponse {
  device: DeviceSnapshot
  /** overall_online 的兼容别名（后端始终返回），前端统一用 overall_online。 */
  device_reachable: boolean
  overall_online: boolean
  heartbeat_online: boolean
  data_plane_online: boolean
  device_status_age_s: number
  hub: StatusHub
}

export interface NotifyChannelConfig {
  bot_token?: string
  bot_token_set?: boolean
  chat_id?: string
  api_base?: string
  to?: string
  url?: string
  secret?: string
  secret_set?: boolean
  token?: string
  sendkey?: string
  channel?: string
  template?: string
  [k: string]: unknown
}

export interface NotifyChannel {
  id: string
  type: ChannelType
  name: string
  enabled: boolean
  config: NotifyChannelConfig
}

export interface Config {
  device_token_tail: string
  tg_manage_enabled: boolean
  notify_channels: NotifyChannel[]
  admin_phone: string
  default_send_sim_id: string
  blacklist: string
  poll_interval: number
  keepalive_interval_days: number
  keepalive_ping_host: string
  tombstone_keep_days: number
  message_keep_days: number
}

export interface NotifyTestResponse {
  ok: boolean
  error: string
}

export interface SendResponse {
  ok: boolean
  queued: boolean
  id: number
  parts: number
  sim_id: string
  device_mac: string
}

export interface AtResponse {
  ok: boolean
  response: string
}

export interface RefreshResponse {
  ok: boolean
  sim_id: string
  device_mac: string
  age_s: number
}

export interface ClearBufferResponse {
  ok: boolean
  sim_id: string
  device_mac: string
  deleted: number
}

export interface OkResponse {
  ok: boolean
  [k: string]: unknown
}

/** new_messages 事件携带的最新一条 SMS 摘要(供通知零请求渲染)。 */
export interface LatestMessagePreview {
  id: number
  sender: string
  brand: string
  code: string | null
  text: string
  content_type: string
}

export interface SSEEvent {
  type: string
  sim_id?: string
  device_mac?: string
  count?: number
  online?: boolean
  id?: number
  latest?: LatestMessagePreview
  [k: string]: unknown
}
