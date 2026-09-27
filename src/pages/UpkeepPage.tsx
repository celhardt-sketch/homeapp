import { useEffect, useState } from 'react'
import { Bell, CheckCircle2, ChevronDown, ChevronUp, Pencil, Plus, Trash2, Wrench, X } from 'lucide-react'
import { api, formatDay, formatFrequency, todayIso, type UpkeepItem, type UpkeepLog } from '../api'

const STATUS_STYLE: Record<UpkeepItem['status'], string> = {
  none: 'bg-stone-100 text-stone-600',
  ok: 'bg-green-100 text-green-800',
  soon: 'bg-amber-100 text-amber-800',
  due: 'bg-red-100 text-red-800',
}

const INTERVALS = [1, 7, 14, 30, 60, 90, 180, 365, 730]
const QUICK_INTERVALS = [1, 7, 30, 365]

function statusLabel(i: UpkeepItem): string {
  if (i.days_left === null) return 'Not logged yet'
  if (i.last_done_on === null) return 'Never logged — due now'
  if (i.days_left < 0) return `Overdue by ${-i.days_left} day${i.days_left === -1 ? '' : 's'}`
  if (i.days_left === 0) return 'Due today'
  return `Due in ${i.days_left} day${i.days_left === 1 ? '' : 's'}`
}

function sortKey(i: UpkeepItem): number {
  return i.days_left ?? 9999
}

export default function UpkeepPage({ userName }: { userName: string }) {
  const [items, setItems] = useState<UpkeepItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<UpkeepItem | null>(null)
  const [showForm, setShowForm] = useState(false)

  useEffect(() => {
    api.upkeep().then(setItems).catch((e: Error) => setError(e.message))
  }, [])

  function upsert(i: UpkeepItem) {
    setItems((prev) => {
      if (!prev) return [i]
      return prev.some((x) => x.id === i.id) ? prev.map((x) => (x.id === i.id ? i : x)) : [...prev, i]
    })
  }

  async function remove(i: UpkeepItem) {
    if (!confirm(`Remove "${i.name}" and its history?`)) return
    try {
      await api.deleteUpkeep(i.id)
      setItems((prev) => prev?.filter((x) => x.id !== i.id) ?? null)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  if (!items) return <p className="text-stone-500">Loading…</p>

  const sorted = [...items].sort((a, b) => sortKey(a) - sortKey(b))
  const dueCount = items.filter((i) => i.status === 'due').length
  const soonCount = items.filter((i) => i.status === 'soon').length

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Home Upkeep</h1>
          <p className="text-sm text-stone-500">
            Recurring whole-house jobs — filters, car oil, screens, gutters. Mark each one done and you'll be reminded when it's due again.
            {dueCount > 0 && <span className="text-red-700"> {dueCount} due now.</span>}
            {soonCount > 0 && <span className="text-amber-700"> {soonCount} coming up.</span>}
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
        <UpkeepForm
          item={editing}
          onSaved={(i) => {
            upsert(i)
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

      {items.length === 0 && !showForm && (
        <p className="rounded-xl bg-white p-6 text-center text-sm text-stone-500 shadow-sm">
          Nothing on the list yet. Add a job like "Change HVAC filter" with how often it repeats.
        </p>
      )}

      <ul className="space-y-3">
        {sorted.map((i) => (
          <UpkeepCard key={i.id} item={i} userName={userName} onChange={upsert} onEdit={() => setEditing(i)} onDelete={() => remove(i)} onError={setError} />
        ))}
      </ul>
    </div>
  )
}

function UpkeepCard({
  item,
  userName,
  onChange,
  onEdit,
  onDelete,
  onError,
}: {
  item: UpkeepItem
  userName: string
  onChange: (i: UpkeepItem) => void
  onEdit: () => void
  onDelete: () => void
  onError: (msg: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [date, setDate] = useState(todayIso())
  const [note, setNote] = useState('')
  const [history, setHistory] = useState<UpkeepLog[] | null>(null)

  useEffect(() => {
    if (open) api.upkeepHistory(item.id).then(setHistory).catch(() => setHistory([]))
  }, [open, item.id])

  async function markDone() {
    try {
      onChange(await api.logUpkeep(item.id, { done_on: date, done_by: userName, note }))
      if (open) setHistory(await api.upkeepHistory(item.id))
      setDate(todayIso())
      setNote('')
    } catch (e) {
      onError((e as Error).message)
    }
  }

  async function removeLog(l: UpkeepLog) {
    if (!confirm(`Remove the entry from ${formatDay(l.done_on)}?`)) return
    try {
      await api.deleteUpkeepLog(l.id)
      const [updated] = await Promise.all([api.upkeep().then((all) => all.find((x) => x.id === item.id)), api.upkeepHistory(item.id).then(setHistory)])
      if (updated) onChange(updated)
    } catch (e) {
      onError((e as Error).message)
    }
  }

  return (
    <li className="rounded-xl bg-white p-4 shadow-sm">
      <div className="flex items-start gap-3">
        <span className={`flex size-10 shrink-0 items-center justify-center rounded-full ${STATUS_STYLE[item.status]}`}>
          <Wrench className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-medium">
            {item.name}
            {item.category && <span className="text-stone-500"> · {item.category}</span>}
          </p>
          <p className="text-xs text-stone-500">
            {formatFrequency(item.interval_days)}
            {item.last_done_on ? (
              <>
                {' · '}last done {formatDay(item.last_done_on)}
                {item.last_done_by && <> by {item.last_done_by}</>}
              </>
            ) : (
              ' · never logged'
            )}
          </p>
          <span className={`mt-1 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[item.status]}`}>
            <Bell className="size-3" /> {statusLabel(item)}
            {item.due_on && item.status !== 'due' && <> ({formatDay(item.due_on)})</>}
          </span>
        </div>
        <button onClick={() => setOpen((v) => !v)} className="p-1.5 text-stone-500" aria-label={open ? 'Collapse' : 'Expand'}>
          {open ? <ChevronUp className="size-5" /> : <ChevronDown className="size-5" />}
        </button>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg bg-stone-50 p-2">
        <label className="text-xs font-medium text-stone-600">Done on</label>
        <input
          type="date"
          value={date}
          max={todayIso()}
          onChange={(e) => setDate(e.target.value)}
          className="rounded-lg border border-stone-300 bg-white px-2 py-1.5 text-sm outline-none focus:border-teal-600"
        />
        <button
          onClick={markDone}
          disabled={!date}
          className="flex items-center gap-1 rounded-lg bg-teal-700 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
        >
          <CheckCircle2 className="size-4" /> Mark done
        </button>
        {open && (
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Optional note (e.g. filter size 16x25x1)"
            className="w-full rounded-lg border border-stone-300 bg-white px-2 py-1.5 text-sm outline-none focus:border-teal-600"
          />
        )}
      </div>

      {open && (
        <div className="mt-3 space-y-3 border-t border-stone-100 pt-3 text-sm">
          {item.notes && <p className="text-stone-600">{item.notes}</p>}
          <p className="text-xs text-stone-500">Reminder {item.interval_days} days after each time it's done.</p>
          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-stone-500">History</h3>
            {history === null && <p className="text-xs text-stone-400">Loading…</p>}
            {history?.length === 0 && <p className="text-xs text-stone-400">Never logged.</p>}
            <ul className="divide-y divide-stone-100">
              {history?.map((l) => (
                <li key={l.id} className="flex items-center justify-between py-1.5 text-xs">
                  <span>
                    {formatDay(l.done_on)}
                    {l.done_by && <span className="text-stone-500"> · {l.done_by}</span>}
                    {l.note && <span className="text-stone-500"> · {l.note}</span>}
                    {l.reminder_sent_at && <span className="text-stone-400"> · reminder emailed</span>}
                  </span>
                  <button onClick={() => removeLog(l)} className="p-1 text-stone-400 hover:text-red-600" aria-label="Remove entry">
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

function UpkeepForm({
  item,
  onSaved,
  onCancel,
  onError,
}: {
  item: UpkeepItem | null
  onSaved: (i: UpkeepItem) => void
  onCancel: () => void
  onError: (msg: string) => void
}) {
  const [name, setName] = useState(item?.name ?? '')
  const [category, setCategory] = useState(item?.category ?? '')
  const [intervalDays, setIntervalDays] = useState(item?.interval_days ?? 90)
  const [notes, setNotes] = useState(item?.notes ?? '')
  const [lastDone, setLastDone] = useState('')

  useEffect(() => {
    setName(item?.name ?? '')
    setCategory(item?.category ?? '')
    setIntervalDays(item?.interval_days ?? 90)
    setNotes(item?.notes ?? '')
    setLastDone('')
  }, [item])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim() || intervalDays < 1) return
    try {
      if (item) {
        onSaved(await api.updateUpkeep(item.id, { name, category, interval_days: intervalDays, notes }))
        return
      }
      const created = await api.createUpkeep({ name, category, interval_days: intervalDays, notes, last_done_on: lastDone || null })
      if (created.duplicate) {
        onError(`"${created.name}" is already on the list — edit that one instead.`)
        onCancel()
        return
      }
      onSaved(created)
    } catch (err) {
      onError((err as Error).message)
    }
  }

  const inputCls = 'w-full rounded-lg border border-stone-300 px-3 py-2 text-sm outline-none focus:border-teal-600'
  const isPreset = INTERVALS.includes(intervalDays)
  return (
    <form onSubmit={submit} className="space-y-2 rounded-xl bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <h2 className="font-medium">{item ? 'Edit job' : 'Add job'}</h2>
        <button type="button" onClick={onCancel} className="text-stone-500" aria-label="Cancel">
          <X className="size-4" />
        </button>
      </div>
      <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Job (e.g. Change HVAC filter)" className={inputCls} />
      <input value={category} onChange={(e) => setCategory(e.target.value)} placeholder="Category (HVAC, Car, Exterior…)" className={inputCls} list="upkeep-categories" />
      <datalist id="upkeep-categories">
        {['HVAC', 'Car', 'Exterior', 'Plumbing', 'Safety', 'Appliances', 'Yard'].map((c) => (
          <option key={c} value={c} />
        ))}
      </datalist>
      <div className="text-xs font-medium text-stone-500">
        Frequency
        <div className="mt-1 flex flex-wrap gap-2">
          {QUICK_INTERVALS.map((d) => (
            <button
              key={d}
              type="button"
              onClick={() => setIntervalDays(d)}
              className={`rounded-full px-3 py-1.5 text-sm ${intervalDays === d ? 'bg-teal-700 text-white' : 'bg-stone-100 text-stone-700'}`}
            >
              {formatFrequency(d)}
            </button>
          ))}
        </div>
        <div className="mt-2 flex items-center gap-2">
          <span>Or every</span>
          <select
            value={isPreset ? intervalDays : 'custom'}
            onChange={(e) => e.target.value !== 'custom' && setIntervalDays(Number(e.target.value))}
            className="rounded-lg border border-stone-300 px-3 py-2 text-sm outline-none focus:border-teal-600"
          >
            {INTERVALS.map((d) => (
              <option key={d} value={d}>
                {formatFrequency(d)}
              </option>
            ))}
            <option value="custom">Custom…</option>
          </select>
          <input
            type="number"
            min={1}
            max={3650}
            value={intervalDays}
            onChange={(e) => setIntervalDays(Number(e.target.value))}
            className="w-20 rounded-lg border border-stone-300 px-3 py-2 text-sm outline-none focus:border-teal-600"
          />
          <span className="text-sm text-stone-600">days</span>
        </div>
      </div>
      {!item && (
        <label className="block text-xs font-medium text-stone-500">
          Last done (optional — sets the first reminder)
          <input type="date" value={lastDone} max={todayIso()} onChange={(e) => setLastDone(e.target.value)} className={`${inputCls} mt-1`} />
        </label>
      )}
      <input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Notes (filter size, oil type, where supplies are…)" className={inputCls} />
      <button
        type="submit"
        disabled={!name.trim() || intervalDays < 1}
        className="flex items-center gap-1 rounded-lg bg-teal-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
      >
        <Plus className="size-4" /> {item ? 'Save' : 'Add job'}
      </button>
    </form>
  )
}
