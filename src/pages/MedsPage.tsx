import { useEffect, useState } from 'react'
import { Bell, CalendarCheck, ChevronDown, ChevronUp, Pencil, Pill, Plus, Trash2, X } from 'lucide-react'
import { api, formatDay, todayIso, type Medication, type Pickup } from '../api'

const STATUS_STYLE: Record<Medication['status'], string> = {
  none: 'bg-stone-100 text-stone-600',
  ok: 'bg-green-100 text-green-800',
  soon: 'bg-amber-100 text-amber-800',
  due: 'bg-red-100 text-red-800',
}

function statusLabel(m: Medication): string {
  if (m.status === 'none' || m.days_left === null) return 'No pickup logged'
  if (m.days_left < 0) return `Reorder overdue by ${-m.days_left} day${m.days_left === -1 ? '' : 's'}`
  if (m.days_left === 0) return 'Reorder today'
  return `Reorder in ${m.days_left} day${m.days_left === 1 ? '' : 's'}`
}

export default function MedsPage({ userName }: { userName: string }) {
  const [meds, setMeds] = useState<Medication[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<Medication | null>(null)
  const [showForm, setShowForm] = useState(false)

  useEffect(() => {
    api.medications().then(setMeds).catch((e: Error) => setError(e.message))
  }, [])

  function upsert(m: Medication) {
    setMeds((prev) => {
      if (!prev) return [m]
      return prev.some((x) => x.id === m.id) ? prev.map((x) => (x.id === m.id ? m : x)) : [...prev, m]
    })
  }

  async function remove(m: Medication) {
    if (!confirm(`Remove "${m.name}" for ${m.person} and its pickup history?`)) return
    try {
      await api.deleteMedication(m.id)
      setMeds((prev) => prev?.filter((x) => x.id !== m.id) ?? null)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  if (!meds) return <p className="text-stone-500">Loading…</p>

  const sorted = [...meds].sort((a, b) => (a.days_left ?? 9999) - (b.days_left ?? 9999))
  const dueCount = meds.filter((m) => m.status === 'due').length

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Medications</h1>
          <p className="text-sm text-stone-500">
            Log each prescription pickup; a reorder reminder is due 28 days later (adjustable per medication).
            {dueCount > 0 && <span className="text-red-700"> {dueCount} need reordering.</span>}
          </p>
        </div>
        <button
          onClick={() => {
            setEditing(null)
            setShowForm(true)
          }}
          className="flex shrink-0 items-center gap-1 rounded-lg bg-teal-700 px-3 py-2 text-sm font-medium text-white"
        >
          <Plus className="size-4" /> Add
        </button>
      </div>

      {error && <p className="rounded-lg bg-red-50 p-2 text-sm text-red-700">{error}</p>}

      {(showForm || editing) && (
        <MedForm
          med={editing}
          onSaved={(m) => {
            upsert(m)
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

      {meds.length === 0 && !showForm && (
        <p className="rounded-xl bg-white p-6 text-center text-sm text-stone-500 shadow-sm">
          No medications yet. Add one, then log the date it was picked up.
        </p>
      )}

      <ul className="space-y-3">
        {sorted.map((m) => (
          <MedCard key={m.id} med={m} userName={userName} onChange={upsert} onEdit={() => setEditing(m)} onDelete={() => remove(m)} onError={setError} />
        ))}
      </ul>
    </div>
  )
}

function MedCard({
  med,
  userName,
  onChange,
  onEdit,
  onDelete,
  onError,
}: {
  med: Medication
  userName: string
  onChange: (m: Medication) => void
  onEdit: () => void
  onDelete: () => void
  onError: (msg: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [date, setDate] = useState(todayIso())
  const [history, setHistory] = useState<Pickup[] | null>(null)

  useEffect(() => {
    if (open) api.pickupHistory(med.id).then(setHistory).catch(() => setHistory([]))
  }, [open, med.id, med.last_pickup_id])

  async function logPickup() {
    try {
      onChange(await api.logPickup(med.id, { picked_up_on: date, picked_up_by: userName }))
      setDate(todayIso())
    } catch (e) {
      onError((e as Error).message)
    }
  }

  async function removePickup(p: Pickup) {
    if (!confirm(`Remove the pickup logged on ${formatDay(p.picked_up_on)}?`)) return
    try {
      await api.deletePickup(p.id)
      const [updated] = await Promise.all([api.medications().then((all) => all.find((x) => x.id === med.id)), api.pickupHistory(med.id).then(setHistory)])
      if (updated) onChange(updated)
    } catch (e) {
      onError((e as Error).message)
    }
  }

  return (
    <li className="rounded-xl bg-white p-4 shadow-sm">
      <div className="flex items-start gap-3">
        <span className={`flex size-10 shrink-0 items-center justify-center rounded-full ${STATUS_STYLE[med.status]}`}>
          <Pill className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-medium">
            {med.name} <span className="text-stone-500">· {med.person}</span>
          </p>
          <p className="text-xs text-stone-500">
            {med.last_picked_up_on ? (
              <>
                Picked up {formatDay(med.last_picked_up_on)}
                {med.last_picked_up_by && <> by {med.last_picked_up_by}</>}
              </>
            ) : (
              'Never logged'
            )}
          </p>
          <span className={`mt-1 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[med.status]}`}>
            <Bell className="size-3" /> {statusLabel(med)}
            {med.reorder_on && med.status !== 'due' && <> ({formatDay(med.reorder_on)})</>}
          </span>
        </div>
        <button onClick={() => setOpen((v) => !v)} className="p-1.5 text-stone-500" aria-label={open ? 'Collapse' : 'Expand'}>
          {open ? <ChevronUp className="size-5" /> : <ChevronDown className="size-5" />}
        </button>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg bg-stone-50 p-2">
        <label className="text-xs font-medium text-stone-600">Picked up on</label>
        <input
          type="date"
          value={date}
          max={todayIso()}
          onChange={(e) => setDate(e.target.value)}
          className="rounded-lg border border-stone-300 bg-white px-2 py-1.5 text-sm outline-none focus:border-teal-600"
        />
        <button
          onClick={logPickup}
          disabled={!date}
          className="flex items-center gap-1 rounded-lg bg-teal-700 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
        >
          <CalendarCheck className="size-4" /> Log pickup
        </button>
      </div>

      {open && (
        <div className="mt-3 space-y-3 border-t border-stone-100 pt-3 text-sm">
          {med.notes && <p className="text-stone-600">{med.notes}</p>}
          <p className="text-xs text-stone-500">Reminder {med.reorder_days} days after each pickup.</p>
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
                    {p.reminder_sent_at && <span className="text-stone-400"> · reminder emailed</span>}
                  </span>
                  <button onClick={() => removePickup(p)} className="p-1 text-stone-400 hover:text-red-600" aria-label="Remove pickup">
                    <Trash2 className="size-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          </div>
          <div className="flex gap-3">
            <button onClick={onEdit} className="flex items-center gap-1 text-teal-700">
              <Pencil className="size-4" /> Edit
            </button>
            <button onClick={onDelete} className="flex items-center gap-1 text-red-700">
              <Trash2 className="size-4" /> Remove
            </button>
          </div>
        </div>
      )}
    </li>
  )
}

function MedForm({
  med,
  onSaved,
  onCancel,
  onError,
}: {
  med: Medication | null
  onSaved: (m: Medication) => void
  onCancel: () => void
  onError: (msg: string) => void
}) {
  const [name, setName] = useState(med?.name ?? '')
  const [person, setPerson] = useState(med?.person ?? '')
  const [reorderDays, setReorderDays] = useState(med?.reorder_days ?? 28)
  const [notes, setNotes] = useState(med?.notes ?? '')

  useEffect(() => {
    setName(med?.name ?? '')
    setPerson(med?.person ?? '')
    setReorderDays(med?.reorder_days ?? 28)
    setNotes(med?.notes ?? '')
  }, [med])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim() || !person.trim()) return
    try {
      const saved = med
        ? await api.updateMedication(med.id, { name, person, reorder_days: reorderDays, notes })
        : await api.createMedication({ name, person, reorder_days: reorderDays, notes })
      onSaved(saved)
    } catch (err) {
      onError((err as Error).message)
    }
  }

  const inputCls = 'w-full rounded-lg border border-stone-300 px-3 py-2 text-sm outline-none focus:border-teal-600'
  return (
    <form onSubmit={submit} className="space-y-2 rounded-xl bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <h2 className="font-medium">{med ? 'Edit medication' : 'Add medication'}</h2>
        <button type="button" onClick={onCancel} className="text-stone-500" aria-label="Cancel">
          <X className="size-4" />
        </button>
      </div>
      <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Medication (e.g. Albuterol inhaler)" className={inputCls} />
      <input value={person} onChange={(e) => setPerson(e.target.value)} placeholder="Who it's for" className={inputCls} />
      <label className="block text-xs font-medium text-stone-500">
        Remind to reorder after
        <div className="mt-1 flex items-center gap-2">
          <input
            type="number"
            min={1}
            max={365}
            value={reorderDays}
            onChange={(e) => setReorderDays(Number(e.target.value))}
            className="w-24 rounded-lg border border-stone-300 px-3 py-2 text-sm outline-none focus:border-teal-600"
          />
          <span className="text-sm text-stone-600">days</span>
        </div>
      </label>
      <input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Notes (pharmacy, dose, Rx number…)" className={inputCls} />
      <button
        type="submit"
        disabled={!name.trim() || !person.trim()}
        className="flex items-center gap-1 rounded-lg bg-teal-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
      >
        <Plus className="size-4" /> {med ? 'Save' : 'Add medication'}
      </button>
    </form>
  )
}
