import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { api, connectEvents } from './api'
import type { Health, Slot, SlotDetail } from './types'
import './App.css'

const emptyForm = {
  name: '',
  slot_name: '',
  host: 'archipelago.gg',
  port: 38281,
  password: '',
  yaml_text: '',
  time_min: 10,
  time_max: 10,
  region_mode: true,
  auto_goal_on_go_mode: false,
  start: true,
}

function statusLabel(status: Slot['status']) {
  switch (status) {
    case 'bk':
      return 'In BK'
    case 'connecting':
      return 'Connecting'
    case 'pending':
      return 'Queued'
    default:
      return status.charAt(0).toUpperCase() + status.slice(1)
  }
}

export default function App() {
  const [slots, setSlots] = useState<Slot[]>([])
  const [health, setHealth] = useState<Health | null>(null)
  const [form, setForm] = useState(emptyForm)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detail, setDetail] = useState<SlotDetail | null>(null)
  const [showForm, setShowForm] = useState(true)

  useEffect(() => {
    api.health().then(setHealth).catch(() => undefined)
    api.listSlots().then(setSlots).catch((e) => setError(String(e.message || e)))
    return connectEvents(setSlots)
  }, [])

  useEffect(() => {
    if (selectedId == null) {
      setDetail(null)
      return
    }
    let cancelled = false
    const load = () => {
      api
        .getSlot(selectedId)
        .then((d) => {
          if (!cancelled) setDetail(d)
        })
        .catch(() => undefined)
    }
    load()
    const id = window.setInterval(load, 2000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [selectedId, slots])

  const running = useMemo(
    () => slots.filter((s) => s.desired_state === 'running' && s.status !== 'completed').length,
    [slots],
  )

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const created = await api.createSlot({
        ...form,
        port: Number(form.port),
        time_min: Number(form.time_min),
        time_max: Number(form.time_max),
      })
      setForm({ ...emptyForm, host: form.host, port: form.port })
      setSelectedId(created.id)
      setShowForm(false)
      setSlots(await api.listSlots())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function act(id: number, action: 'start' | 'stop' | 'restart' | 'delete') {
    setError('')
    try {
      if (action === 'delete') {
        await api.deleteSlot(id)
        if (selectedId === id) setSelectedId(null)
      } else if (action === 'start') {
        await api.startSlot(id)
      } else if (action === 'stop') {
        await api.stopSlot(id)
      } else {
        await api.restartSlot(id)
      }
      setSlots(await api.listSlots())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  function onYamlFile(file: File | null) {
    if (!file) return
    file.text().then((text) => {
      setForm((f) => ({
        ...f,
        yaml_text: text,
        name: f.name || file.name.replace(/\.(ya?ml)$/i, ''),
      }))
    })
  }

  return (
    <div className="page">
      <header className="hero">
        <div className="hero-copy">
          <p className="eyebrow">Hey There, this is the...</p>
          <h1>Slow Release Webclient</h1>
          <p className="lede">
            Feed a player YAML and room details. Workers keep releasing checks until the slot is done.
          </p>
          <div className="hero-actions">
            <button type="button" className="btn primary" onClick={() => setShowForm(true)}>
              Add slot
            </button>
            <span className="hero-meta">
              {running} active · {slots.length} total
              {health ? ` · workers ${health.running_workers}/${health.max_workers}` : ''}
            </span>
          </div>
          {health && !health.tracker_available && (
            <p className="banner warn">
              Universal Tracker missing: {health.tracker_error || 'install the UT apworld to run workers.'}
            </p>
          )}
        </div>
      </header>

      {error && <div className="banner error">{error}</div>}

      {showForm && (
        <section className="panel form-panel" aria-labelledby="add-slot-title">
          <div className="panel-head">
            <h2 id="add-slot-title">New release slot</h2>
            <button type="button" className="btn ghost" onClick={() => setShowForm(false)}>
              Close
            </button>
          </div>
          <form className="slot-form" onSubmit={onSubmit}>
            <label>
              Display name
              <input
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="My Zelda slot"
              />
            </label>
            <label>
              Slot name
              <input
                required
                value={form.slot_name}
                onChange={(e) => setForm({ ...form, slot_name: e.target.value })}
                placeholder="Player1"
              />
            </label>
            <label>
              Host
              <input
                required
                value={form.host}
                onChange={(e) => setForm({ ...form, host: e.target.value })}
              />
            </label>
            <label>
              Port
              <input
                type="number"
                required
                value={form.port}
                onChange={(e) => setForm({ ...form, port: Number(e.target.value) })}
              />
            </label>
            <label>
              Password
              <input
                type="password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                placeholder="Optional"
              />
            </label>
            <label>
              Time min (s)
              <input
                type="number"
                step="0.1"
                min="0.1"
                value={form.time_min}
                onChange={(e) => setForm({ ...form, time_min: Number(e.target.value) })}
              />
            </label>
            <label>
              Time max (s)
              <input
                type="number"
                step="0.1"
                min="0.1"
                value={form.time_max}
                onChange={(e) => setForm({ ...form, time_max: Number(e.target.value) })}
              />
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={form.region_mode}
                onChange={(e) => setForm({ ...form, region_mode: e.target.checked })}
              />
              Region mode
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={form.auto_goal_on_go_mode}
                onChange={(e) => setForm({ ...form, auto_goal_on_go_mode: e.target.checked })}
              />
              Auto-goal in go mode
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={form.start}
                onChange={(e) => setForm({ ...form, start: e.target.checked })}
              />
              Start immediately
            </label>
            <label className="full">
              Player YAML
              <textarea
                required
                rows={10}
                value={form.yaml_text}
                onChange={(e) => setForm({ ...form, yaml_text: e.target.value })}
                placeholder="Paste player YAML here…"
              />
            </label>
            <label className="full file">
              Or upload YAML
              <input
                type="file"
                accept=".yaml,.yml,text/yaml,text/plain"
                onChange={(e) => onYamlFile(e.target.files?.[0] || null)}
              />
            </label>
            <div className="form-actions full">
              <button className="btn primary" disabled={busy} type="submit">
                {busy ? 'Saving…' : form.start ? 'Create & start' : 'Create slot'}
              </button>
            </div>
          </form>
        </section>
      )}

      <section className="slots" aria-label="Release slots">
        {slots.length === 0 ? (
          <p className="empty">No slots yet. Add one to begin a slow release.</p>
        ) : (
          <ul className="slot-list">
            {slots.map((slot, index) => (
              <li
                key={slot.id}
                className={`slot-row status-${slot.status} ${selectedId === slot.id ? 'selected' : ''}`}
                style={{ animationDelay: `${index * 40}ms` }}
              >
                <button type="button" className="slot-main" onClick={() => setSelectedId(slot.id)}>
                  <div className="slot-title">
                    <strong>{slot.name}</strong>
                    <span className={`chip ${slot.status}`}>{statusLabel(slot.status)}</span>
                  </div>
                  <div className="slot-sub">
                    {slot.slot_name} @ {slot.host}:{slot.port}
                    {slot.current_location ? ` · ${slot.current_location}` : ''}
                  </div>
                  <div className="bar" aria-hidden="true">
                    <span style={{ width: `${Math.min(100, slot.progress_pct)}%` }} />
                  </div>
                  <div className="slot-progress">
                    {slot.checked_count}/{slot.total_count || '—'} checks
                    {slot.available_count ? ` · ${slot.available_count} in logic` : ''}
                    {slot.last_error ? ` · ${slot.last_error}` : ''}
                  </div>
                </button>
                <div className="slot-actions">
                  {slot.desired_state === 'running' && slot.status !== 'completed' ? (
                    <button type="button" className="btn" onClick={() => act(slot.id, 'stop')}>
                      Stop
                    </button>
                  ) : (
                    <button type="button" className="btn" onClick={() => act(slot.id, 'start')}>
                      Start
                    </button>
                  )}
                  <button type="button" className="btn" onClick={() => act(slot.id, 'restart')}>
                    Restart
                  </button>
                  <button type="button" className="btn danger" onClick={() => act(slot.id, 'delete')}>
                    Delete
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {detail && (
        <aside className="detail panel" aria-label="Slot detail">
          <div className="panel-head">
            <h2>{detail.name}</h2>
            <button type="button" className="btn ghost" onClick={() => setSelectedId(null)}>
              Close
            </button>
          </div>
          <dl className="meta">
            <div>
              <dt>Connection</dt>
              <dd>
                {detail.slot_name} → {detail.host}:{detail.port}
              </dd>
            </div>
            <div>
              <dt>Timing</dt>
              <dd>
                {detail.time_min}–{detail.time_max}s · region {detail.region_mode ? 'on' : 'off'}
                {' · '}
                auto-goal {detail.auto_goal_on_go_mode ? 'on' : 'off'}
              </dd>
            </div>
            <div>
              <dt>Region</dt>
              <dd>{detail.current_region || '—'}</dd>
            </div>
            <div>
              <dt>Restarts</dt>
              <dd>{detail.restart_count}</dd>
            </div>
          </dl>
          <h3>Logs</h3>
          <pre className="logs">{detail.logs.length ? detail.logs.join('\n') : 'No log lines yet.'}</pre>
        </aside>
      )}

      <footer className="site-footer">
        <p>© {new Date().getFullYear()} SylvaNova LLC. All rights reserved.</p>
      </footer>
    </div>
  )
}
