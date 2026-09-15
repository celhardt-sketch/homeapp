import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Copy, Nfc, Pencil, Plus, Trash2, X } from 'lucide-react'
import { api, formatFrequency, type Room, type RoomSummary, type Task } from '../api'
import { ROOM_ICONS, RoomIcon } from '../icons'

const FREQUENCIES: { label: string; value: number | null }[] = [
  { label: 'As needed', value: null },
  { label: 'Daily', value: 1 },
  { label: 'Weekly', value: 7 },
  { label: 'Every 2 weeks', value: 14 },
  { label: 'Monthly', value: 30 },
  { label: 'Every 3 months', value: 90 },
  { label: 'Every 6 months', value: 180 },
  { label: 'Yearly', value: 365 },
]

export default function AdminPage() {
  const [rooms, setRooms] = useState<RoomSummary[]>([])
  const [selected, setSelected] = useState<Room | null>(null)
  const [error, setError] = useState<string | null>(null)

  const loadRooms = useCallback(() => {
    api.rooms().then(setRooms).catch((e: Error) => setError(e.message))
  }, [])
  useEffect(loadRooms, [loadRooms])

  async function openRoom(slug: string) {
    setSelected(await api.room(slug))
  }

  async function withError(fn: () => Promise<void>) {
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Manage</h1>
        <p className="text-sm text-stone-500">
          Each room has a tag link. Write that link to an NFC tag and stick it in the room — scanning it opens
          the room's task list.
        </p>
      </div>

      {error && <p className="rounded-lg bg-red-50 p-2 text-sm text-red-700">{error}</p>}

      {!selected ? (
        <>
          <ul className="space-y-2">
            {rooms.map((r) => (
              <li key={r.id} className="flex items-center gap-3 rounded-xl bg-white p-3 shadow-sm">
                <span className="flex size-9 items-center justify-center rounded-lg bg-teal-50 text-teal-700">
                  <RoomIcon name={r.icon} className="size-5" />
                </span>
                <button onClick={() => openRoom(r.slug)} className="flex-1 text-left">
                  <span className="block font-medium">{r.name}</span>
                  <span className="block text-xs text-stone-500">
                    {r.task_count} task{r.task_count === 1 ? '' : 's'} · tag id: {r.slug}
                  </span>
                </button>
                <button onClick={() => openRoom(r.slug)} className="p-2 text-stone-500" aria-label="Edit room">
                  <Pencil className="size-4" />
                </button>
              </li>
            ))}
          </ul>
          <NewRoomForm
            onCreate={(body) =>
              withError(async () => {
                const room = await api.createRoom(body)
                loadRooms()
                setSelected(room)
              })
            }
          />
        </>
      ) : (
        <RoomEditor
          room={selected}
          onBack={() => {
            setSelected(null)
            loadRooms()
          }}
          onChange={setSelected}
          onDelete={() =>
            withError(async () => {
              await api.deleteRoom(selected.id)
              setSelected(null)
              loadRooms()
            })
          }
          onError={setError}
        />
      )}
    </div>
  )
}

function NewRoomForm({ onCreate }: { onCreate: (body: { name: string; icon: string }) => void }) {
  const [name, setName] = useState('')
  const [icon, setIcon] = useState('home')
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        if (!name.trim()) return
        onCreate({ name, icon })
        setName('')
      }}
      className="rounded-xl bg-white p-4 shadow-sm"
    >
      <h2 className="mb-2 font-medium">Add a room</h2>
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Room name (e.g. Master Bath)"
        className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm outline-none focus:border-teal-600"
      />
      <IconPicker value={icon} onChange={setIcon} />
      <button
        type="submit"
        disabled={!name.trim()}
        className="mt-3 flex items-center gap-1 rounded-lg bg-teal-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
      >
        <Plus className="size-4" /> Add room
      </button>
    </form>
  )
}

function IconPicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {Object.entries(ROOM_ICONS).map(([key, { icon: Icon, label }]) => (
        <button
          type="button"
          key={key}
          title={label}
          onClick={() => onChange(key)}
          className={`flex size-9 items-center justify-center rounded-lg border ${
            value === key ? 'border-teal-600 bg-teal-50 text-teal-700' : 'border-stone-200 text-stone-500'
          }`}
        >
          <Icon className="size-4" />
        </button>
      ))}
    </div>
  )
}

function RoomEditor({
  room,
  onBack,
  onChange,
  onDelete,
  onError,
}: {
  room: Room
  onBack: () => void
  onChange: (r: Room) => void
  onDelete: () => void
  onError: (msg: string) => void
}) {
  const [name, setName] = useState(room.name)
  const [slug, setSlug] = useState(room.slug)
  const [icon, setIcon] = useState(room.icon)
  const [copied, setCopied] = useState(false)
  const tagUrl = `${window.location.origin}/r/${room.slug}`

  async function save() {
    try {
      onChange(await api.updateRoom(room.id, { name, slug, icon }))
    } catch (e) {
      onError((e as Error).message)
    }
  }

  async function copy() {
    await navigator.clipboard.writeText(tagUrl)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  const dirty = name !== room.name || slug !== room.slug || icon !== room.icon

  return (
    <div className="space-y-4">
      <button onClick={onBack} className="text-sm text-teal-700">
        ← All rooms
      </button>

      <section className="rounded-xl bg-white p-4 shadow-sm">
        <h2 className="mb-3 font-medium">Room details</h2>
        <label className="block text-xs font-medium text-stone-500">Name</label>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="mt-1 w-full rounded-lg border border-stone-300 px-3 py-2 text-sm outline-none focus:border-teal-600"
        />
        <label className="mt-3 block text-xs font-medium text-stone-500">Tag id (used in the link)</label>
        <input
          value={slug}
          onChange={(e) => setSlug(e.target.value)}
          className="mt-1 w-full rounded-lg border border-stone-300 px-3 py-2 font-mono text-sm outline-none focus:border-teal-600"
        />
        <IconPicker value={icon} onChange={setIcon} />
        <div className="mt-3 flex items-center justify-between">
          <button
            onClick={save}
            disabled={!dirty || !name.trim() || !slug.trim()}
            className="rounded-lg bg-teal-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
          >
            Save
          </button>
          <button
            onClick={() => {
              if (confirm(`Delete "${room.name}" and all of its tasks and history?`)) onDelete()
            }}
            className="flex items-center gap-1 text-sm text-red-700"
          >
            <Trash2 className="size-4" /> Delete room
          </button>
        </div>
      </section>

      <section className="rounded-xl border border-teal-200 bg-teal-50 p-4">
        <h2 className="mb-1 flex items-center gap-2 font-medium text-teal-900">
          <Nfc className="size-5" /> NFC tag link
        </h2>
        <p className="mb-2 text-sm text-teal-800">
          Write this URL to the room's NFC tag (use an app like "NFC Tools" on iPhone or Android, choose
          "Write" → "URL"). Scanning the tag will open this room.
        </p>
        <div className="flex items-center gap-2">
          <code className="flex-1 truncate rounded-lg bg-white px-3 py-2 text-sm">{tagUrl}</code>
          <button onClick={copy} className="rounded-lg bg-white p-2 text-teal-800" aria-label="Copy link">
            <Copy className="size-4" />
          </button>
        </div>
        {copied && <p className="mt-1 text-xs text-teal-800">Copied!</p>}
        <Link to={`/r/${room.slug}`} className="mt-2 inline-block text-sm text-teal-800 underline">
          Open room page
        </Link>
      </section>

      <TaskEditor room={room} onChange={onChange} onError={onError} />
    </div>
  )
}

function TaskEditor({
  room,
  onChange,
  onError,
}: {
  room: Room
  onChange: (r: Room) => void
  onError: (msg: string) => void
}) {
  const [editing, setEditing] = useState<Task | null>(null)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [frequency, setFrequency] = useState<number | null>(30)

  function startEdit(t: Task) {
    setEditing(t)
    setTitle(t.title)
    setDescription(t.description)
    setFrequency(t.frequency_days)
  }

  function reset() {
    setEditing(null)
    setTitle('')
    setDescription('')
    setFrequency(30)
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!title.trim()) return
    try {
      if (editing) {
        const updated = await api.updateTask(editing.id, { title, description, frequency_days: frequency ?? 0 })
        onChange({ ...room, tasks: room.tasks.map((t) => (t.id === updated.id ? updated : t)) })
      } else {
        const created = await api.createTask({ room_id: room.id, title, description, frequency_days: frequency })
        onChange({ ...room, tasks: [...room.tasks, created] })
      }
      reset()
    } catch (err) {
      onError((err as Error).message)
    }
  }

  async function remove(t: Task) {
    if (!confirm(`Delete "${t.title}"?`)) return
    try {
      await api.deleteTask(t.id)
      onChange({ ...room, tasks: room.tasks.filter((x) => x.id !== t.id) })
      if (editing?.id === t.id) reset()
    } catch (err) {
      onError((err as Error).message)
    }
  }

  return (
    <section className="rounded-xl bg-white p-4 shadow-sm">
      <h2 className="mb-3 font-medium">Tasks</h2>
      <ul className="mb-4 divide-y divide-stone-100">
        {room.tasks.length === 0 && <li className="py-2 text-sm text-stone-400">No tasks yet.</li>}
        {room.tasks.map((t) => (
          <li key={t.id} className="flex items-center gap-2 py-2">
            <div className="flex-1">
              <p className="text-sm font-medium">{t.title}</p>
              <p className="text-xs text-stone-500">{formatFrequency(t.frequency_days)}</p>
            </div>
            <button onClick={() => startEdit(t)} className="p-1.5 text-stone-500" aria-label="Edit task">
              <Pencil className="size-4" />
            </button>
            <button onClick={() => remove(t)} className="p-1.5 text-red-600" aria-label="Delete task">
              <Trash2 className="size-4" />
            </button>
          </li>
        ))}
      </ul>

      <form onSubmit={submit} className="space-y-2 rounded-lg bg-stone-50 p-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium">{editing ? 'Edit task' : 'Add a task'}</h3>
          {editing && (
            <button type="button" onClick={reset} className="text-stone-500" aria-label="Cancel edit">
              <X className="size-4" />
            </button>
          )}
        </div>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Task (e.g. Change furnace filter)"
          className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm outline-none focus:border-teal-600"
        />
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Details (optional)"
          className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm outline-none focus:border-teal-600"
        />
        <select
          value={frequency ?? ''}
          onChange={(e) => setFrequency(e.target.value === '' ? null : Number(e.target.value))}
          className="w-full rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm outline-none focus:border-teal-600"
        >
          {FREQUENCIES.map((f) => (
            <option key={f.label} value={f.value ?? ''}>
              {f.label}
            </option>
          ))}
        </select>
        <button
          type="submit"
          disabled={!title.trim()}
          className="flex items-center gap-1 rounded-lg bg-teal-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
        >
          <Plus className="size-4" /> {editing ? 'Save task' : 'Add task'}
        </button>
      </form>
    </section>
  )
}
