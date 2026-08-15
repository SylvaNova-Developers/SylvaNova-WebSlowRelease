import type { Health, Slot, SlotCreatePayload, SlotDetail } from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail || JSON.stringify(body)
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  if (res.status === 204) {
    return undefined as T
  }
  return res.json() as Promise<T>
}

export const api = {
  health: () => request<Health>('/api/health'),
  listSlots: () => request<Slot[]>('/api/slots'),
  getSlot: (id: number) => request<SlotDetail>(`/api/slots/${id}`),
  createSlot: (payload: SlotCreatePayload) =>
    request<Slot>('/api/slots', { method: 'POST', body: JSON.stringify(payload) }),
  patchSlot: (
    id: number,
    payload: {
      auto_goal_on_go_mode?: boolean
      region_mode?: boolean
      time_min?: number
      time_max?: number
    },
  ) => request<Slot>(`/api/slots/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  startSlot: (id: number) => request<Slot>(`/api/slots/${id}/start`, { method: 'POST' }),
  stopSlot: (id: number) => request<Slot>(`/api/slots/${id}/stop`, { method: 'POST' }),
  restartSlot: (id: number) => request<Slot>(`/api/slots/${id}/restart`, { method: 'POST' }),
  deleteSlot: (id: number) => request<void>(`/api/slots/${id}`, { method: 'DELETE' }),
}

export function connectEvents(onSlots: (slots: Slot[]) => void): () => void {
  let ws: WebSocket | null = null
  let closed = false
  let retry = 0
  let timer: number | undefined

  const connect = () => {
    if (closed) return
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    ws = new WebSocket(`${proto}://${location.host}/api/events`)
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data)
        if (msg.type === 'hello' || msg.type === 'slots') {
          onSlots(msg.slots as Slot[])
          retry = 0
        }
      } catch {
        /* ignore */
      }
    }
    ws.onclose = () => {
      if (closed) return
      const delay = Math.min(10000, 1000 * 2 ** retry)
      retry += 1
      timer = window.setTimeout(connect, delay)
    }
  }

  connect()

  // Fallback poll if WS is down
  const poll = window.setInterval(() => {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      api.listSlots().then(onSlots).catch(() => undefined)
    }
  }, 5000)

  return () => {
    closed = true
    window.clearInterval(poll)
    if (timer) window.clearTimeout(timer)
    ws?.close()
  }
}
