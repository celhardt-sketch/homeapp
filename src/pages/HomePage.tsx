import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronRight, StickyNote, Wrench } from 'lucide-react'
import { api, formatDate, formatDay, type ActivityItem, type RoomSummary, type UpkeepItem } from '../api'
import { RoomIcon } from '../icons'

export default function HomePage({ isAdmin }: { isAdmin: boolean }) {
  const [rooms, setRooms] = useState<RoomSummary[] | null>(null)
  const [activity, setActivity] = useState<ActivityItem[]>([])
  const [upkeepDue, setUpkeepDue] = useState<UpkeepItem[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.rooms().then(setRooms).catch((e: Error) => setError(e.message))
    if (isAdmin) api.activity().then(setActivity).catch(() => {})
    api
      .upkeep()
      .then((items) => setUpkeepDue(items.filter((i) => i.status === 'due' || i.status === 'soon').sort((a, b) => (a.days_left ?? 0) - (b.days_left ?? 0))))
      .catch(() => {})
  }, [isAdmin])

  if (error) return <p className="text-red-700">Couldn't load rooms: {error}</p>
  if (!rooms) return <p className="text-stone-500">Loading…</p>

  return (
    <div className="space-y-6">
      {upkeepDue.length > 0 && (
        <Link to="/upkeep" className="block rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm active:bg-amber-100">
          <span className="flex items-center gap-2 font-medium text-amber-900">
            <Wrench className="size-4" /> Home upkeep coming due
            <ChevronRight className="ml-auto size-4 text-amber-700" />
          </span>
          <ul className="mt-1 space-y-0.5 text-amber-800">
            {upkeepDue.slice(0, 4).map((i) => (
              <li key={i.id}>
                {i.name} ·{' '}
                {i.days_left !== null && i.days_left <= 0 ? <span className="font-medium text-red-700">{i.days_left === 0 ? 'due today' : `overdue ${-i.days_left}d`}</span> : `due ${formatDay(i.due_on)}`}
              </li>
            ))}
            {upkeepDue.length > 4 && <li>+{upkeepDue.length - 4} more</li>}
          </ul>
        </Link>
      )}

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
