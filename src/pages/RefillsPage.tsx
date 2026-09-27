import { useEffect, useState } from 'react'
import { AlertTriangle, CalendarCheck, ChevronDown, ChevronUp, Pencil, Phone, PhoneCall, Pill, Plus, Trash2, X } from 'lucide-react'
import { api, formatDay, todayIso, type Child, type Pickup, type Prescription, type PrescriptionInput, type RefillStatus, type User } from '../api'

const STATUS_STYLE: Record<RefillStatus, string> = {
  no_pickup: 'bg-stone-100 text-stone-600',
  ok: 'bg-green-100 text-green-800',
  refill_due: 'bg-amber-100 text-amber-900',
  called_waiting: 'bg-sky-100 text-sky-900',
  urgent: 'bg-red-100 text-red-800',
}

const STATUS_ORDER: Record<RefillStatus, number> = { urgent: 0, refill_due: 1, called_waiting: 2, ok: 3, no_pickup: 4 }

function statusLabel(p: Prescription): string {
  switch (p.refill_status) {
    case 'no_pickup':
      return 'No pickup logged yet'
    case 'urgent':
      return p.days_of_supply_left === 0 ? 'URGENT: supply runs out today' : `URGENT: out of supply ${-(p.days_of_supply_left ?? 0)} day${p.days_of_supply_left === -1 ? '' : 's'} ago`
    case 'refill_due':
      return `Refill due · ${p.days_of_supply_left} day${p.days_of_supply_left === 1 ? '' : 's'} of supply left`
    case 'called_waiting':
      return `Called, waiting · ${p.days_of_supply_left} day${p.days_of_supply_left === 1 ? '' : 's'} left`
    default:
      return `OK · refill in ${(p.refill_after_days - (p.days_since_pickup ?? 0))} day${p.refill_after_days - (p.days_since_pickup ?? 0) === 1 ? '' : 's'}`
  }
}

export default function RefillsPage({ isAdmin }: { isAdmin: boolean }) {
  const [rxs, setRxs] = useState<Prescription[] | null>(null)
  const [children, setChildren] = useState<Child[]>([])
  const [users, setUsers] = useState<User[]>([])
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<Prescription | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [showInactive, setShowInactive] = useState(false)

  useEffect(() => {
    Promise.all([api.prescriptions(showInactive), api.children()])
      .then(([p, c]) => {
        setRxs(p)
        setChildren(c)
      })
      .catch((e: Error) => setError(e.message))
    if (isAdmin) api.users().then(setUsers).catch(() => setUsers([]))
  }, [isAdmin, showInactive])

  function upsert(p: Prescription) {
    setRxs((prev) => {
      if (!prev) return [p]
      const next = prev.some((x) => x.id === p.id) ? prev.map((x) => (x.id === p.id ? p : x)) : [...prev, p]
      return showInactive ? next : next.filter((x) => x.active)
    })
  }

  if (!rxs) return <p className="text-stone-500">{error ?? 'Loading…'}</p>

  const sorted = [...rxs].sort(
    (a, b) => STATUS_ORDER[a.refill_status] - STATUS_ORDER[b.refill_status] || (a.days_of_supply_left ?? 9999) - (b.days_of_supply_left ?? 9999),
  )
  const due = sorted.filter((p) => p.refill_status === 'refill_due' || p.refill_status === 'urgent')
  const urgentCount = due.filter((p) => p.refill_status === 'urgent').length

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Prescription refills</h1>
          <p className="text-sm text-stone-500">
            Log each pickup; a refill is due {`\u2265`}28 days later and stays on this list until the next pickup is logged.
          </p>
        </div>
        {isAdmin && (
          <button
            onClick={() => {
              setEditing(null)
              setShowForm(true)
            }}
            className="flex shrink-0 items-center gap-1 rounded-lg bg-teal-700 px-3 py-2 text-sm font-medium text-white"
          >
            <Plus className="size-4" /> Add
          </button>
        )}
      </div>

      {error && <p className="rounded-lg bg-red-50 p-2 text-sm text-red-700">{error}</p>}

      {due.length > 0 && (
        <div className={`rounded-xl p-3 text-sm ${urgentCount ? 'bg-red-50 text-red-900' : 'bg-amber-50 text-amber-900'}`}>
          <p className="flex items-center gap-2 font-medium">
            <AlertTriangle className="size-4" /> {due.length} refill{due.length === 1 ? '' : 's'} to call in
            {urgentCount > 0 && <> · {urgentCount} urgent (supply is out)</>}
          </p>
          <ul className="mt-1 list-inside list-disc text-xs">
            {due.map((p) => (
              <li key={p.id}>
                {p.child} · {p.name} · {statusLabel(p)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {(showForm || editing) && isAdmin && (
        <PrescriptionForm
          rx={editing}
          kids={children}
          users={users}
          onChildAdded={(c) =>
            setChildren((prev) =>
              prev.some((k) => k.id === c.id) ? prev : [...prev, c].sort((a, b) => a.name.localeCompare(b.name)),
            )
          }
          onSaved={(p) => {
            upsert(p)
            setShowForm(false)
            setEditing(null)
          }}
          onCancel={() => {
            setShowForm(false)
            setEditing(null)
          }}
          onError={setError}
        />
      )}

      {rxs.length === 0 && !showForm && (
        <p className="rounded-xl bg-white p-6 text-center text-sm text-stone-500 shadow-sm">
          No prescriptions yet.{isAdmin ? ' Add one, then log the date it was picked up.' : ' Ask an admin to add them.'}
        </p>
      )}

      <ul className="space-y-3">
        {sorted.map((p) => (
          <PrescriptionCard key={p.id} rx={p} isAdmin={isAdmin} onChange={upsert} onEdit={() => setEditing(p)} onError={setError} />
        ))}
      </ul>

      {isAdmin && (
        <label className="flex items-center gap-2 text-xs text-stone-500">
          <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} /> Show deactivated prescriptions
        </label>
      )}
    </div>
  )
}

function PrescriptionCard({
  rx,
  isAdmin,
  onChange,
  onEdit,
  onError,
}: {
  rx: Prescription
  isAdmin: boolean
  onChange: (p: Prescription) => void
  onEdit: () => void
  onError: (msg: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [date, setDate] = useState(todayIso())
  const [pickupNotes, setPickupNotes] = useState('')
  const [calledNotes, setCalledNotes] = useState('')
  const [history, setHistory] = useState<Pickup[] | null>(null)

  useEffect(() => {
    if (open) api.pickupHistory(rx.id).then(setHistory).catch(() => setHistory([]))
  }, [open, rx.id])

  async function logPickup(override = false) {
    try {
      onChange(await api.logPickup(rx.id, { picked_up_on: date, notes: pickupNotes, override }))
      if (open) setHistory(await api.pickupHistory(rx.id))
      setDate(todayIso())
      setPickupNotes('')
    } catch (e) {
      const msg = (e as Error).message
      if (!override && msg.includes('override') && confirm(`${msg}\n\nLog it anyway?`)) return logPickup(true)
      onError(msg)
    }
  }

  async function markCalled() {
    try {
      onChange(await api.markCalled(rx.id, calledNotes))
      setCalledNotes('')
    } catch (e) {
      onError((e as Error).message)
    }
  }

  async function removePickup(p: Pickup) {
    if (!confirm(`Remove the pickup logged on ${formatDay(p.picked_up_on)}?`)) return
    try {
      await api.deletePickup(p.id)
      const [all, hist] = await Promise.all([api.prescriptions(true), api.pickupHistory(rx.id)])
      setHistory(hist)
      const updated = all.find((x) => x.id === rx.id)
      if (updated) onChange(updated)
    } catch (e) {
      onError((e as Error).message)
    }
  }

  async function setActive(active: boolean) {
    if (!active && !confirm(`Deactivate ${rx.name} for ${rx.child}? History is kept; it stops appearing on the refill list.`)) return
    try {
      onChange(await api.updatePrescription(rx.id, { active }))
    } catch (e) {
      onError((e as Error).message)
    }
  }

  const needsCall = rx.refill_status === 'refill_due' || rx.refill_status === 'urgent'
  const contact = rx.contact_name || rx.pharmacy

  return (
    <li className={`rounded-xl bg-white p-4 shadow-sm ${rx.refill_status === 'urgent' ? 'ring-2 ring-red-300' : ''} ${rx.active ? '' : 'opacity-60'}`}>
      <div className="flex items-start gap-3">
        <span className={`flex size-10 shrink-0 items-center justify-center rounded-full ${STATUS_STYLE[rx.refill_status]}`}>
          <Pill className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-medium">
            {rx.child} <span className="text-stone-500">· {rx.name}</span>
            {!rx.active && <span className="ml-1 text-xs text-stone-400">(deactivated)</span>}
          </p>
          <p className="text-xs text-stone-500">
            {rx.last_picked_up_on ? (
              <>
                Picked up {formatDay(rx.last_picked_up_on)} ({rx.days_since_pickup} day{rx.days_since_pickup === 1 ? '' : 's'} ago)
                {rx.last_picked_up_by && <> by {rx.last_picked_up_by}</>}
              </>
            ) : (
              'Never logged'
            )}
          </p>
          <span className={`mt-1 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[rx.refill_status]}`}>
            {rx.refill_status === 'urgent' ? <AlertTriangle className="size-3" /> : rx.refill_status === 'called_waiting' ? <PhoneCall className="size-3" /> : null}
            {statusLabel(rx)}
          </span>
          {rx.refill_status === 'called_waiting' && (
            <p className="mt-1 text-xs text-sky-900">
              Called {formatDay(rx.called_on)}
              {rx.called_by && <> by {rx.called_by}</>}
              {rx.called_notes && <> · {rx.called_notes}</>}. Reminders are quiet for 2 days, then it comes back until a pickup is logged.
            </p>
          )}
        </div>
        <button onClick={() => setOpen((v) => !v)} className="p-1.5 text-stone-500" aria-label={open ? 'Collapse' : 'Expand'}>
          {open ? <ChevronUp className="size-5" /> : <ChevronDown className="size-5" />}
        </button>
      </div>

      {contact && (
        <p className="mt-2 flex items-center gap-1 text-sm text-stone-700">
          <Phone className="size-3.5 text-stone-400" />
          Call {contact}
          {rx.contact_name && rx.pharmacy && <span className="text-stone-500"> (pharmacy: {rx.pharmacy})</span>}
          {rx.contact_phone && (
            <>
              {' · '}
              <a href={`tel:${rx.contact_phone.replace(/[^\d+]/g, '')}`} className="font-medium text-teal-700 underline">
                {rx.contact_phone}
              </a>
            </>
          )}
        </p>
      )}

      {rx.active && (
        <div className="mt-3 space-y-2">
          <div className="flex flex-wrap items-center gap-2 rounded-lg bg-stone-50 p-2">
            <label className="text-xs font-medium text-stone-600">Picked up on</label>
            <input
              type="date"
              value={date}
              max={todayIso()}
              onChange={(e) => setDate(e.target.value)}
              className="rounded-lg border border-stone-300 bg-white px-2 py-1.5 text-sm outline-none focus:border-teal-600"
            />
            <input
              value={pickupNotes}
              onChange={(e) => setPickupNotes(e.target.value)}
              placeholder="Note (optional)"
              className="min-w-0 flex-1 rounded-lg border border-stone-300 bg-white px-2 py-1.5 text-sm outline-none focus:border-teal-600"
            />
            <button
              onClick={() => logPickup()}
              disabled={!date}
              className="flex items-center gap-1 rounded-lg bg-teal-700 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
            >
              <CalendarCheck className="size-4" /> Log pickup
            </button>
          </div>
          {needsCall && (
            <div className="flex flex-wrap items-center gap-2 rounded-lg bg-sky-50 p-2">
              <input
                value={calledNotes}
                onChange={(e) => setCalledNotes(e.target.value)}
                placeholder="e.g. ready Thursday (optional)"
                className="min-w-0 flex-1 rounded-lg border border-stone-300 bg-white px-2 py-1.5 text-sm outline-none focus:border-teal-600"
              />
              <button onClick={markCalled} className="flex items-center gap-1 rounded-lg border border-sky-700 px-3 py-1.5 text-sm font-medium text-sky-800">
                <PhoneCall className="size-4" /> I called, waiting
              </button>
              <p className="w-full text-xs text-sky-900">
                {rx.refill_status === 'urgent'
                  ? 'Supply is out: marking called does not quiet urgent reminders.'
                  : 'Quiets the daily reminder for 2 days; it comes back if no pickup is logged.'}
              </p>
            </div>
          )}
        </div>
      )}

      {open && (
        <div className="mt-3 space-y-3 border-t border-stone-100 pt-3 text-sm">
          {rx.notes && <p className="text-stone-600">{rx.notes}</p>}
          <p className="text-xs text-stone-500">
            {rx.days_supply}-day supply, refill allowed after day {rx.refill_after_days}.
            {rx.assignee && <> Reminders also go to {rx.assignee}.</>}
          </p>
          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-stone-500">Pickup history</h3>
            {history === null && <p className="text-xs text-stone-400">Loading…</p>}
            {history?.length === 0 && <p className="text-xs text-stone-400">No pickups logged.</p>}
            <ul className="divide-y divide-stone-100">
              {history?.map((p) => (
                <li key={p.id} className="flex items-center justify-between py-1.5 text-xs">
                  <span>
                    {formatDay(p.picked_up_on)}
                    {p.picked_up_by && <span className="text-stone-500"> · {p.picked_up_by}</span>}
                    {p.notes && <span className="text-stone-500"> · {p.notes}</span>}
                  </span>
                  <button onClick={() => removePickup(p)} className="p-1 text-stone-400 hover:text-red-600" aria-label="Remove pickup">
                    <Trash2 className="size-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          </div>
          {isAdmin && (
            <div className="flex gap-3">
              <button onClick={onEdit} className="flex items-center gap-1 text-teal-700">
                <Pencil className="size-4" /> Edit
              </button>
              {rx.active ? (
                <button onClick={() => setActive(false)} className="flex items-center gap-1 text-red-700">
                  <X className="size-4" /> Deactivate
                </button>
              ) : (
                <button onClick={() => setActive(true)} className="flex items-center gap-1 text-teal-700">
                  Reactivate
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </li>
  )
}

function PrescriptionForm({
  rx,
  kids,
  users,
  onChildAdded,
  onSaved,
  onCancel,
  onError,
}: {
  rx: Prescription | null
  kids: Child[]
  users: User[]
  onChildAdded: (c: Child) => void
  onSaved: (p: Prescription) => void
  onCancel: () => void
  onError: (msg: string) => void
}) {
  const [form, setForm] = useState<PrescriptionInput>(() => toInput(rx, kids))
  const [newChild, setNewChild] = useState('')

  useEffect(() => setForm(toInput(rx, kids)), [rx, kids])

  function set<K extends keyof PrescriptionInput>(key: K, value: PrescriptionInput[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  async function addChild() {
    if (!newChild.trim()) return
    try {
      const c = await api.createChild(newChild.trim())
      onChildAdded(c)
      set('child_id', c.id)
      setNewChild('')
    } catch (e) {
      onError((e as Error).message)
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!form.name.trim() || !form.child_id) return
    try {
      if (rx) {
        onSaved(await api.updatePrescription(rx.id, form))
        return
      }
      const created = await api.createPrescription(form)
      if (created.duplicate) onError(`${created.child} already has "${created.name}", so nothing was added.`)
      onSaved(created)
    } catch (err) {
      onError((err as Error).message)
    }
  }

  const input = 'w-full rounded-lg border border-stone-300 px-3 py-2 text-sm outline-none focus:border-teal-600'

  return (
    <form onSubmit={submit} className="space-y-3 rounded-xl bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <h2 className="font-medium">{rx ? 'Edit prescription' : 'New prescription'}</h2>
        <button type="button" onClick={onCancel} className="p-1 text-stone-500" aria-label="Cancel">
          <X className="size-5" />
        </button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block text-sm">
          <span className="mb-1 block text-xs font-medium text-stone-600">Child</span>
          <select value={form.child_id || ''} onChange={(e) => set('child_id', Number(e.target.value))} className={input} required>
            <option value="">Choose…</option>
            {kids.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-sm">
          <span className="mb-1 block text-xs font-medium text-stone-600">Or add a child</span>
          <span className="flex gap-2">
            <input value={newChild} onChange={(e) => setNewChild(e.target.value)} placeholder="Name" className={input} />
            <button type="button" onClick={addChild} disabled={!newChild.trim()} className="rounded-lg border border-teal-700 px-3 text-sm text-teal-700 disabled:opacity-40">
              Add
            </button>
          </span>
        </label>
      </div>
      <label className="block text-sm">
        <span className="mb-1 block text-xs font-medium text-stone-600">Prescription</span>
        <input value={form.name} onChange={(e) => set('name', e.target.value)} className={input} required autoFocus />
      </label>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="block text-sm">
          <span className="mb-1 block text-xs font-medium text-stone-600">Pharmacy</span>
          <input value={form.pharmacy ?? ''} onChange={(e) => set('pharmacy', e.target.value)} className={input} />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block text-xs font-medium text-stone-600">Who to call</span>
          <input value={form.contact_name ?? ''} onChange={(e) => set('contact_name', e.target.value)} placeholder="Pharmacy or prescriber's office" className={input} />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block text-xs font-medium text-stone-600">Phone</span>
          <input value={form.contact_phone ?? ''} onChange={(e) => set('contact_phone', e.target.value)} type="tel" className={input} />
        </label>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="block text-sm">
          <span className="mb-1 block text-xs font-medium text-stone-600">Days of supply</span>
          <input type="number" min={1} max={365} value={form.days_supply ?? 30} onChange={(e) => set('days_supply', Number(e.target.value))} className={input} />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block text-xs font-medium text-stone-600">Refill allowed after day</span>
          <input type="number" min={1} max={365} value={form.refill_after_days ?? 28} onChange={(e) => set('refill_after_days', Number(e.target.value))} className={input} />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block text-xs font-medium text-stone-600">Also remind</span>
          <select value={form.assignee_id ?? ''} onChange={(e) => set('assignee_id', e.target.value ? Number(e.target.value) : null)} className={input}>
            <option value="">Courtney only</option>
            {users.filter((u) => u.active).map((u) => (
              <option key={u.id} value={u.id}>
                {u.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="block text-sm">
        <span className="mb-1 block text-xs font-medium text-stone-600">Notes</span>
        <textarea value={form.notes ?? ''} onChange={(e) => set('notes', e.target.value)} rows={2} className={input} />
      </label>
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onCancel} className="rounded-lg px-3 py-2 text-sm text-stone-600">
          Cancel
        </button>
        <button type="submit" className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-medium text-white">
          {rx ? 'Save' : 'Add prescription'}
        </button>
      </div>
    </form>
  )
}

function toInput(rx: Prescription | null, children: Child[]): PrescriptionInput {
  return {
    child_id: rx?.child_id ?? (children.length === 1 ? children[0].id : 0),
    name: rx?.name ?? '',
    pharmacy: rx?.pharmacy ?? '',
    contact_name: rx?.contact_name ?? '',
    contact_phone: rx?.contact_phone ?? '',
    days_supply: rx?.days_supply ?? 30,
    refill_after_days: rx?.refill_after_days ?? 28,
    notes: rx?.notes ?? '',
    assignee_id: rx?.assignee_id ?? null,
  }
}
