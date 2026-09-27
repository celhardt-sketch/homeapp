import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Check, ShoppingCart, Trash2 } from 'lucide-react'
import { api, formatDay, formatFrequency, type ListItem, type PersonList, type User } from '../api'

export default function MyListPage({ me }: { me: User }) {
  const [person, setPerson] = useState<User>(me)
  const [users, setUsers] = useState<User[]>([])
  const [list, setList] = useState<PersonList | null>(null)
  const [includeDone, setIncludeDone] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const mine = person.id === me.id

  const load = useCallback(() => {
    setError(null)
    const p = mine ? api.myList(includeDone) : api.userList(person.id, includeDone)
    p.then(setList).catch((e: Error) => setError(e.message))
  }, [mine, person.id, includeDone])
  useEffect(load, [load])
  useEffect(() => {
    api.users().then(setUsers).catch(() => setUsers([]))
  }, [])

  async function finish(item: ListItem) {
    setError(null)
    try {
      if (item.kind === 'task') await api.completeTask(item.id)
      else await api.updateShoppingItem(item.id, { bought: true })
      load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function remove(item: ListItem) {
    if (!confirm(`Delete "${item.title}"?`)) return
    setError(null)
    try {
      if (item.kind === 'task') await api.deleteTask(item.id)
      else await api.deleteShoppingItem(item.id)
      load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">{mine ? 'My list' : `${person.name}'s list`}</h1>
        <p className="text-sm text-stone-500">
          {mine ? 'Tasks and things to buy that are yours' : `What ${person.name} has been asked to do`}, oldest first.
        </p>
      </div>

      {users.length > 1 && (
        <div className="flex flex-wrap gap-2">
          {users.map((u) => (
            <button
              key={u.id}
              onClick={() => setPerson(u)}
              className={`rounded-full px-3 py-1 text-sm ${u.id === person.id ? 'bg-teal-700 text-white' : 'bg-stone-100 text-stone-700'}`}
            >
              {u.id === me.id ? 'Me' : u.name}
            </button>
          ))}
        </div>
      )}

      {error && <p className="rounded-lg bg-red-50 p-2 text-sm text-red-700">{error}</p>}
      {!list && !error && <p className="text-stone-500">Loading…</p>}

      {list && (
        <>
          {list.overdue_count > 0 && (
            <p className="rounded-lg bg-red-50 p-2 text-sm text-red-700">
              {list.overdue_count} overdue item{list.overdue_count === 1 ? '' : 's'}.
            </p>
          )}
          {list.items.length === 0 && <p className="text-stone-500">Nothing on the list right now.</p>}
          <ul className="space-y-2">
            {list.items.map((item) => (
              <li
                key={`${item.kind}-${item.id}`}
                className={`flex items-start gap-3 rounded-xl bg-white p-4 shadow-sm ${item.overdue && !item.done ? 'ring-1 ring-red-200' : ''} ${
                  item.done ? 'opacity-60' : ''
                }`}
              >
                <button
                  onClick={() => finish(item)}
                  disabled={item.done || !mine}
                  aria-label={item.kind === 'task' ? `Mark "${item.title}" done` : `Mark "${item.title}" bought`}
                  title={mine ? undefined : 'Only the person it belongs to can check this off'}
                  className={`mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border-2 ${
                    item.done
                      ? 'border-emerald-500 bg-emerald-500 text-white'
                      : 'border-stone-300 text-transparent hover:border-teal-600 hover:text-teal-600 disabled:hover:border-stone-300 disabled:hover:text-transparent'
                  }`}
                >
                  <Check className="size-4" />
                </button>
                <div className="flex-1">
                  <p className={`flex items-center gap-1.5 ${item.done ? 'line-through' : ''}`}>
                    {item.kind === 'shopping' && <ShoppingCart className="size-4 text-stone-400" />}
                    {item.title}
                  </p>
                  <p className="mt-1 text-xs text-stone-500">
                    {item.kind === 'shopping' ? 'To buy' : item.room_slug ? <Link to={`/r/${item.room_slug}`} className="text-teal-700 underline">{item.room_name}</Link> : 'Errand'}
                    {item.kind === 'task' && item.frequency_days && <> · {formatFrequency(item.frequency_days)}</>}
                    {item.due_on && (
                      <>
                        {' '}
                        · due {formatDay(item.due_on)}
                        {item.overdue && !item.done && <span className="font-medium text-red-700"> (overdue)</span>}
                      </>
                    )}
                    {!item.due_on && item.overdue && !item.done && <span className="font-medium text-red-700"> · overdue</span>}
                    {item.notes && <> · {item.notes}</>}
                    {item.done && item.last_completed_by && <> · done by {item.last_completed_by}</>}
                  </p>
                </div>
                {(mine || me.role === 'admin') && (
                  <button onClick={() => remove(item)} className="p-1.5 text-stone-400 hover:text-red-600" aria-label={`Delete "${item.title}"`}>
                    <Trash2 className="size-4" />
                  </button>
                )}
              </li>
            ))}
          </ul>
          <label className="flex items-center gap-2 text-sm text-stone-500">
            <input type="checkbox" checked={includeDone} onChange={(e) => setIncludeDone(e.target.checked)} />
            Show finished items
          </label>
        </>
      )}
    </div>
  )
}
