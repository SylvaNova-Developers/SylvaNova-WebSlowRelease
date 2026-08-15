export type SlotStatus =
  | 'pending'
  | 'connecting'
  | 'running'
  | 'bk'
  | 'completed'
  | 'error'
  | 'stopped'

export interface Slot {
  id: number
  name: string
  slot_name: string
  host: string
  port: number
  has_password: boolean
  time_min: number
  time_max: number
  region_mode: boolean
  auto_goal_on_go_mode: boolean
  desired_state: 'running' | 'stopped'
  status: SlotStatus
  checked_count: number
  total_count: number
  available_count: number
  current_location: string
  current_region: string
  last_check_at: string | null
  last_error: string
  pid: number | null
  heartbeat_at: string | null
  restart_count: number
  created_at: string
  updated_at: string
  progress_pct: number
}

export interface SlotDetail extends Slot {
  yaml_text: string
  logs: string[]
}

export interface Health {
  ok: boolean
  tracker_available: boolean
  tracker_error: string
  version: string
  max_workers: number
  running_workers: number
}

export interface SlotCreatePayload {
  name: string
  slot_name: string
  host: string
  port: number
  password: string
  yaml_text: string
  time_min: number
  time_max: number
  region_mode: boolean
  auto_goal_on_go_mode: boolean
  start: boolean
}
