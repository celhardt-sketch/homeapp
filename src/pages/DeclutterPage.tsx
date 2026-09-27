import { useEffect, useState } from 'react'
import { Boxes, CheckCircle2, Pencil, Plus, RotateCcw, Trash2, X } from 'lucide-react'
import { api, formatDay, type DeclutterSpot, type RoomSummary } from '../api'

export default function DeclutterPage() {
  const [spots, setSpots] = useState<DeclutterSpot[] | null>(null)
  const [rooms, setRooms] = useState<RoomSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<DeclutterSpot | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [showDone, setShowDone] = useState(false)

  useEffect(() => {
    api.declutter(true).then(setSpots).catch((e: Error) => setError(e.message))
    api.rooms().then(setRooms).catch(() => {})
  }, [])

  function upsert(s: DeclutterSpot) {
    setSpots((prev) => {
      if (!prev) return [s]
      return prev.some((x) => x.id === s.id) ? prev.map((x) => (x.id === s.id ? s : x)) : [...prev, s]
    })
  }

  async function toggleDone(s: DeclutterSpot) {
    try {
      upsert(await api.updateDeclutter(s.id, { done: !s.done }))
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function remove(s: DeclutterSpot) {
    if (!confirm(`Remove "${s.name}" from the declutter list?`)) return
    try {
      await api.deleteDeclutter(s.id)
      setSpots((prev) => prev?.filter((x) => x.id !== s.id) ?? null)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  if (!spots) return <p className="text-stone-500">Loading…</p>

  const open = spots.filter((s) => !s.done).sort((a, b) => a.id - b.id)
  const done = spots.filter((s) => s.done).sort((a, b) => (b.done_on ?? '').localeCompare(a.done_on ?? '') || b.id - a.id)

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Declutter</h1>
          <p className="text-sm text-stone-500">
            Drawers, closets, shelves and boxes to go through. Check one off when it's been cleared out.
            {open.length > 0 && <span className="text-teal-700"> {open.length} left.</span>}
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
        <SpotForm
          spot={editing}
          rooms={rooms}
          onSaved={(s) => {
            upsert(s)
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

      {open.length === 0 && !showForm && (
        <p className="rounded-xl bg-white p-6 text-center text-sm text-stone-500 shadow-sm">
          {spots.length === 0 ? 'Nothing on the list yet. Add a spot like "Junk drawer" or "Hall closet".' : 'All clear — nothing left to declutter.'}
        </p>
      )}

      <ul className="space-y-2">
        {open.map((s) => (
          <SpotRow key={s.id} spot={s} onToggle={() => toggleDone(s)} onEdit={() => setEditing(s)} onDelete={() => remove(s)} />
        ))}
      </ul>

      {done.length > 0 && (
        <div className="space-y-2">
          <button onClick={() => setShowDone((v) => !v)} className="text-sm font-medium text-stone-600">
            {showDone ? 'Hide' : 'Show'} {done.length} done
          </button>
          {showDone && (
            <ul className="space-y-2">
              {done.map((s) => (
                <SpotRow key={s.id} spot={s} onToggle={() => toggleDone(s)} onEdit={() => setEditing(s)} onDelete={() => remove(s)} />
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

function SpotRow({ spot, onToggle, onEdit, onDelete }: { spot: DeclutterSpot; onToggle: () => void; onEdit: () => void; onDelete: () => void }) {
  return (
    <li className={`flex items-start gap-3 rounded-xl bg-white p-3 shadow-sm ${spot.done ? 'opacity-70' : ''}`}>
      <button
        onClick={onToggle}
        aria-label={spot.done ? `Put ${spot.name} back on the list` : `Mark ${spot.name} done`}
        className={`mt-0.5 shrink-0 rounded-full ${spot.done ? 'text-green-600' : 'text-stone-300 hover:text-teal-600'}`}
      >
        {spot.done ? <CheckCircle2 className="size-6" /> : <Boxes className="size-6" />}
      </button>
      <div className="min-w-0 flex-1">
        <p className={`font-medium ${spot.done ? 'line-through' : ''}`}>{spot.name}</p>
        <p className="text-xs text-stone-500">
          {spot.room ?? 'No room'}
          {spot.done && spot.done_by && ` · ${spot.done_by} on ${formatDay(spot.done_on)}`}
        </p>
        {spot.notes && <p className="mt-1 whitespace-pre-wrap text-sm text-stone-600">{spot.notes}</p>}
      </div>
      <div className="flex shrink-0 gap-1">
        {spot.done && (
          <button onClick={onToggle} aria-label="Do again" className="rounded p-1 text-stone-400 hover:text-stone-700">
            <RotateCcw className="size-4" />
          </button>
        )}
        <button onClick={onEdit} aria-label="Edit" className="rounded p-1 text-stone-400 hover:text-stone-700">
          <Pencil className="size-4" />
        </button>
        <button onClick={onDelete} aria-label="Delete" className="rounded p-1 text-stone-400 hover:text-red-600">
          <Trash2 className="size-4" />
        </button>
      </div>
    </li>
  )
}

function SpotForm({
  spot,
  rooms,
  onSaved,
  onCancel,
  onError,
}: {
  spot: DeclutterSpot | null
  rooms: RoomSummary[]
  onSaved: (s: DeclutterSpot) => void
  onCancel: () => void
  onError: (msg: string) => void
}) {
  const [name, setName] = useState(spot?.name ?? '')
  const [roomId, setRoomId] = useState<string>(spot?.room_id?.toString() ?? '')
  const [notes, setNotes] = useState(spot?.notes ?? '')
  const [saving, setSaving] = useState(false)
  const [dupNotice, setDupNotice] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    setSaving(true)
    setDupNotice(null)
    const room_id = roomId ? Number(roomId) : null
    try {
      if (spot) {
        onSaved(await api.updateDeclutter(spot.id, { name: name.trim(), room_id, notes }))
      } else {
        const created = await api.createDeclutter({ name: name.trim(), room_id, notes })
        if (created.duplicate) {
          setDupNotice(`"${created.name}" is already on the list${created.room ? ` for ${created.room}` : ''}.`)
        } else {
          onSaved(created)
        }
      }
    } catch (err) {
      onError((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3 rounded-xl border border-teal-200 bg-teal-50/50 p-4">
      <div className="flex items-center justify-between">
        <h2 className="font-medium">{spot ? 'Edit spot' : 'Add a spot to declutter'}</h2>
        <button type="button" onClick={onCancel} aria-label="Cancel" className="text-stone-500">
          <X className="size-5" />
        </button>
      </div>
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Junk drawer, hall closet, boxes under the stairs…"
        required
        maxLength={120}
        autoFocus
        className="w-full rounded-lg border border-stone-300 px-3 py-2"
      />
      <select value={roomId} onChange={(e) => setRoomId(e.target.value)} className="w-full rounded-lg border border-stone-300 bg-white px-3 py-2">
        <option value="">No room</option>
        {rooms.map((r) => (
          <option key={r.id} value={r.id}>
            {r.name}
          </option>
        ))}
      </select>
      <textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder="Notes (optional)"
        maxLength={500}
        rows={2}
        className="w-full rounded-lg border border-stone-300 px-3 py-2"
      />
      {dupNotice && <p className="rounded-lg bg-amber-50 p-2 text-sm text-amber-800">{dupNotice}</p>}
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onCancel} className="rounded-lg px-3 py-2 text-sm text-stone-600">
          Cancel
        </button>
        <button type="submit" disabled={saving} className="rounded-lg bg-teal-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-50">
          {spot ? 'Save' : 'Add'}
        </button>
      </div>
    </form>
  )
}
