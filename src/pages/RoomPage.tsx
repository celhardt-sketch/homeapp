import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Check, ChevronDown, ChevronUp, History, ShoppingCart, StickyNote, Undo2 } from 'lucide-react'
import { api, formatDate, formatFrequency, type Completion, type Room, type Task } from '../api'
import { RoomIcon } from '../icons'
import StatusBadge from '../components/StatusBadge'

export default function RoomPage({ userName }: { userName: string }) {
  const { slug = '' } = useParams()
  const [room, setRoom] = useState<Room | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    api.room(slug).then(setRoom).catch((e: Error) => setError(e.message))
  }, [slug])

  useEffect(load, [load])

  function replaceTask(updated: Task) {
    setRoom((prev) => prev && { ...prev, tasks: prev.tasks.map((t) => (t.id === updated.id ? updated : t)) })
  }

  if (error) return <p className="text-red-700">Couldn't load this room: {error}</p>
  if (!room) return <p className="text-stone-500">Loading…</p>

  const due = room.tasks.filter((t) => t.status !== 'ok')
  const ok = room.tasks.filter((t) => t.status === 'ok')

  return (
    <div>
      <div className="mb-4 flex items-center gap-3">
        <span className="flex size-12 items-center justify-center rounded-xl bg-teal-700 text-white">
          <RoomIcon name={room.icon} className="size-6" />
        </span>
        <div>
          <h1 className="text-2xl font-semibold leading-tight">{room.name}</h1>
          <p className="text-sm text-stone-500">
            {due.length === 0 ? 'Everything is up to date' : `${due.length} task${due.length === 1 ? '' : 's'} due`}
          </p>
        </div>
      </div>

      {room.tasks.length === 0 && <p className="text-stone-500">No tasks in this room yet.</p>}

      <ul className="space-y-2">
        {[...due, ...ok].map((t) => (
          <TaskCard key={t.id} task={t} userName={userName} onChange={replaceTask} onReload={load} />
        ))}
      </ul>
    </div>
  )
}

function TaskCard({
  task,
  userName,
  onChange,
  onReload,
}: {
  task: Task
  userName: string
  onChange: (t: Task) => void
  onReload: () => void
}) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [justDone, setJustDone] = useState<Completion | null>(null)
  const [history, setHistory] = useState<Completion[] | null>(null)
  const [noteBody, setNoteBody] = useState('')
  const [needsPurchase, setNeedsPurchase] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function run(fn: () => Promise<void>) {
    setBusy(true)
    setErr(null)
    try {
      await fn()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const complete = () =>
    run(async () => {
      const updated = await api.completeTask(task.id, userName)
      onChange(updated)
      const [latest] = await api.history(task.id)
      setJustDone(latest ?? null)
      setHistory(null)
    })

  const undo = () =>
    run(async () => {
      if (!justDone) return
      await api.undoCompletion(justDone.id)
      setJustDone(null)
      onReload()
    })

  const addNote = () =>
    run(async () => {
      if (!noteBody.trim()) return
      const note = await api.addNote(task.id, { author: userName, body: noteBody, needs_purchase: needsPurchase })
      onChange({ ...task, notes: [note, ...task.notes] })
      setNoteBody('')
      setNeedsPurchase(false)
    })

  const resolveNote = (id: number) =>
    run(async () => {
      await api.resolveNote(id)
      onChange({ ...task, notes: task.notes.filter((n) => n.id !== id) })
    })

  const toggleHistory = () =>
    run(async () => {
      if (history) setHistory(null)
      else setHistory(await api.history(task.id))
    })

  const isDone = task.status === 'ok'

  return (
    <li className={`rounded-xl bg-white shadow-sm ${task.status === 'overdue' ? 'ring-1 ring-red-200' : ''}`}>
      <div className="flex items-start gap-3 p-4">
        <button
          onClick={complete}
          disabled={busy || !userName}
          aria-label={`Mark "${task.title}" done`}
          className={`mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full border-2 transition ${
            isDone
              ? 'border-emerald-500 bg-emerald-500 text-white'
              : 'border-stone-300 text-transparent hover:border-teal-600 hover:text-teal-600 active:bg-teal-50'
          }`}
        >
          <Check className="size-5" />
        </button>

        <button onClick={() => setOpen((o) => !o)} className="flex-1 text-left">
          <div className="flex items-center gap-2">
            <span className={`font-medium ${isDone ? 'text-stone-500' : ''}`}>{task.title}</span>
            <StatusBadge status={task.status} />
          </div>
          <p className="mt-0.5 text-sm text-stone-500">
            {formatFrequency(task.frequency_days)} · Last done: {formatDate(task.last_completed_at)}
            {task.last_completed_by && <> by {task.last_completed_by}</>}
          </p>
          {task.notes.length > 0 && !open && (
            <p className="mt-1 flex items-center gap-1 text-sm text-amber-700">
              <StickyNote className="size-3.5" /> {task.notes.length} note{task.notes.length === 1 ? '' : 's'}
              {task.notes.some((n) => n.needs_purchase) && ' · something to buy'}
            </p>
          )}
        </button>

        <button onClick={() => setOpen((o) => !o)} className="p-1 text-stone-400" aria-label="Details">
          {open ? <ChevronUp className="size-5" /> : <ChevronDown className="size-5" />}
        </button>
      </div>

      {justDone && (
        <div className="flex items-center justify-between border-t border-emerald-100 bg-emerald-50 px-4 py-2 text-sm text-emerald-800">
          <span>Logged as done by {justDone.completed_by}</span>
          <button onClick={undo} disabled={busy} className="flex items-center gap-1 font-medium underline">
            <Undo2 className="size-4" /> Undo
          </button>
        </div>
      )}

      {err && <p className="px-4 pb-2 text-sm text-red-700">{err}</p>}

      {open && (
        <div className="space-y-4 border-t border-stone-100 px-4 py-3">
          {task.description && <p className="text-sm text-stone-600">{task.description}</p>}

          <div>
            <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-stone-500">Notes</h4>
            {task.notes.length === 0 && <p className="text-sm text-stone-400">No open notes.</p>}
            <ul className="space-y-1.5">
              {task.notes.map((n) => (
                <li key={n.id} className="flex items-start gap-2 rounded-lg bg-stone-50 p-2 text-sm">
                  {n.needs_purchase && <ShoppingCart className="mt-0.5 size-4 shrink-0 text-amber-700" />}
                  <div className="flex-1">
                    <p>{n.body}</p>
                    <p className="text-xs text-stone-500">
                      {n.author} · {formatDate(n.created_at)}
                    </p>
                  </div>
                  <button
                    onClick={() => resolveNote(n.id)}
                    disabled={busy}
                    className="text-xs text-teal-700 underline"
                  >
                    Resolve
                  </button>
                </li>
              ))}
            </ul>
            <div className="mt-2 space-y-2">
              <textarea
                value={noteBody}
                onChange={(e) => setNoteBody(e.target.value)}
                rows={2}
                placeholder="Add a note (e.g. need a new filter, size 16x25)"
                className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm outline-none focus:border-teal-600"
              />
              <div className="flex items-center justify-between">
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={needsPurchase}
                    onChange={(e) => setNeedsPurchase(e.target.checked)}
                    className="size-4 accent-teal-700"
                  />
                  Something needs to be purchased
                </label>
                <button
                  onClick={addNote}
                  disabled={busy || !noteBody.trim() || !userName}
                  className="rounded-lg bg-teal-700 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
                >
                  Add note
                </button>
              </div>
            </div>
          </div>

          <div>
            <button onClick={toggleHistory} className="flex items-center gap-1 text-sm text-teal-700">
              <History className="size-4" /> {history ? 'Hide history' : 'Show history'}
            </button>
            {history && (
              <ul className="mt-2 divide-y divide-stone-100 text-sm">
                {history.length === 0 && <li className="py-1 text-stone-400">Never completed.</li>}
                {history.map((h) => (
                  <li key={h.id} className="flex justify-between py-1">
                    <span>{h.completed_by}</span>
                    <span className="text-stone-500">{new Date(h.completed_at).toLocaleString()}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </li>
  )
}
