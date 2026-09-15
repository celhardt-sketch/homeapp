import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronRight, StickyNote } from 'lucide-react'
import { api, formatDate, type ActivityItem, type RoomSummary } from '../api'
import { RoomIcon } from '../icons'

export default function HomePage() {
  const [rooms, setRooms] = useState<RoomSummary[] | null>(null)
  const [activity, setActivity] = useState<ActivityItem[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.rooms().then(setRooms).catch((e: Error) => setError(e.message))
    api.activity().then(setActivity).catch(() => {})
  }, [])

  if (error) return <p className="text-red-700">Couldn't load rooms: {error}</p>
  if (!rooms) return <p className="text-stone-500">Loading…</p>

  return (
    <div className="space-y-6">
      <section>
        <h1 className="mb-3 text-xl font-semibold">Rooms</h1>
        {rooms.length === 0 && (
          <p className="text-stone-500">
            No rooms yet. <Link to="/admin" className="text-teal-700 underline">Add one</Link>.
          </p>
        )}
        <ul className="space-y-2">
          {rooms.map((r) => (
            <li key={r.id}>
              <Link
                to={`/r/${r.slug}`}
                className="flex items-center gap-3 rounded-xl bg-white p-4 shadow-sm active:bg-stone-50"
              >
                <span className="flex size-10 items-center justify-center rounded-lg bg-teal-50 text-teal-700">
                  <RoomIcon name={r.icon} className="size-5" />
                </span>
                <span className="flex-1">
                  <span className="block font-medium">{r.name}</span>
                  <span className="block text-sm text-stone-500">
                    {r.task_count} task{r.task_count === 1 ? '' : 's'}
                    {r.due_count > 0 && <span className="text-amber-700"> · {r.due_count} due</span>}
                    {r.note_count > 0 && (
                      <span className="inline-flex items-center gap-0.5">
                        {' '}· <StickyNote className="size-3" /> {r.note_count}
                      </span>
                    )}
                  </span>
                </span>
                <ChevronRight className="size-5 text-stone-400" />
              </Link>
            </li>
          ))}
        </ul>
      </section>

      {activity.length > 0 && (
        <section>
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-stone-500">Recent activity</h2>
          <ul className="divide-y divide-stone-200 rounded-xl bg-white shadow-sm">
            {activity.slice(0, 10).map((a) => (
              <li key={a.id} className="px-4 py-2.5 text-sm">
                <span className="font-medium">{a.completed_by}</span> completed{' '}
                <span className="font-medium">{a.task_title}</span>{' '}
                <span className="text-stone-500">
                  in {a.room_name} · {formatDate(a.completed_at)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
