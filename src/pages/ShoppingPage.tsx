import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Check, Plus } from 'lucide-react'
import { api, formatDate, formatDay, type PantryItem, type PurchaseNote, type ShoppingItem, type User } from '../api'

export default function ShoppingPage({ userName, canAssign, myUserId }: { userName: string; canAssign: boolean; myUserId: number | null }) {
  const [items, setItems] = useState<PurchaseNote[] | null>(null)
  const [pantry, setPantry] = useState<PantryItem[] | null>(null)
  const [standalone, setStandalone] = useState<ShoppingItem[] | null>(null)
  const [users, setUsers] = useState<User[]>([])
  const [name, setName] = useState('')
  const [assignee, setAssignee] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.shopping().then(setItems).catch(() => setItems([]))
    api.pantry().then((all) => setPantry(all.filter((p) => p.low))).catch(() => setPantry([]))
    api.shoppingItems().then(setStandalone).catch(() => setStandalone([]))
    api.users().then(setUsers).catch(() => setUsers([]))
  }, [])

  async function markBought(item: PurchaseNote) {
    await api.resolveNote(item.id)
    setItems((prev) => prev?.filter((i) => i.id !== item.id) ?? null)
  }

  async function markItemBought(item: ShoppingItem) {
    await api.updateShoppingItem(item.id, { bought: true })
    setStandalone((prev) => prev?.filter((i) => i.id !== item.id) ?? null)
  }

  async function addItem(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    setError(null)
    try {
      const created = await api.createShoppingItem({ name: name.trim(), assignee_id: canAssign ? assignee : assignee === myUserId ? assignee : null })
      setStandalone((prev) => [...(prev ?? []), created])
      setName('')
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function markStocked(item: PantryItem) {
    await api.updatePantryItem(item.id, { low: false, updated_by: userName })
    setPantry((prev) => prev?.filter((i) => i.id !== item.id) ?? null)
  }

  if (!items || !pantry || !standalone) return <p className="text-stone-500">Loading…</p>

  const empty = items.length === 0 && pantry.length === 0 && standalone.length === 0

  return (
    <div className="space-y-6">
      <div>
        <h1 className="mb-1 text-xl font-semibold">To Buy</h1>
        <p className="text-sm text-stone-500">Things to pick up, things flagged for a task, and pantry items running low.</p>
      </div>

      <form onSubmit={addItem} className="flex flex-wrap gap-2 rounded-xl bg-white p-3 shadow-sm">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Something to buy"
          className="min-w-0 flex-1 rounded-lg border border-stone-300 px-3 py-2 text-sm outline-none focus:border-teal-600"
        />
        <select
          value={assignee ?? ''}
          onChange={(e) => setAssignee(e.target.value === '' ? null : Number(e.target.value))}
          className="rounded-lg border border-stone-300 bg-white px-2 py-2 text-sm outline-none focus:border-teal-600"
        >
          <option value="">Anyone</option>
          {users
            .filter((u) => canAssign || u.id === myUserId)
            .map((u) => (
              <option key={u.id} value={u.id}>
                {u.id === myUserId ? 'Me' : u.name}
              </option>
            ))}
        </select>
        <button
          type="submit"
          disabled={!name.trim()}
          className="flex items-center gap-1 rounded-lg bg-teal-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
          aria-label="Add to list"
        >
          <Plus className="size-4" /> Add
        </button>
        {error && <p className="w-full rounded-lg bg-red-50 p-2 text-sm text-red-700">{error}</p>}
      </form>

      {empty && <p className="text-stone-500">Nothing on the list.</p>}

      {standalone.length > 0 && (
        <section>
          <h2 className="mb-1 px-1 text-xs font-semibold uppercase tracking-wide text-stone-500">To pick up</h2>
          <ul className="space-y-2">
            {standalone.map((item) => (
              <li key={item.id} className={`flex items-start gap-3 rounded-xl bg-white p-4 shadow-sm ${item.overdue ? 'ring-1 ring-red-200' : ''}`}>
                <CheckButton onClick={() => markItemBought(item)} label="Mark as bought" />
                <div className="flex-1">
                  <p>{item.name}</p>
                  <p className="mt-1 text-xs text-stone-500">
                    {item.assignee ? `For ${item.assignee}` : 'Anyone'}
                    {item.due_on && <> · by {formatDay(item.due_on)}{item.overdue && <span className="text-red-700"> (overdue)</span>}</>}
                    {item.notes && <> · {item.notes}</>}
                    {item.added_by && <> · added by {item.added_by}</>}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {pantry.length > 0 && (
        <section>
          <h2 className="mb-1 px-1 text-xs font-semibold uppercase tracking-wide text-stone-500">Pantry</h2>
          <ul className="space-y-2">
            {pantry.map((item) => (
              <li key={item.id} className="flex items-start gap-3 rounded-xl bg-white p-4 shadow-sm">
                <CheckButton onClick={() => markStocked(item)} label="Mark as restocked" />
                <div className="flex-1">
                  <p>{item.name}</p>
                  <p className="mt-1 text-xs text-stone-500">
                    {item.category || 'Pantry'}
                    {item.quantity && <> · usually {item.quantity}</>}
                    {item.updated_by && <> · flagged by {item.updated_by}</>}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {items.length > 0 && (
        <section>
          <h2 className="mb-1 px-1 text-xs font-semibold uppercase tracking-wide text-stone-500">For tasks</h2>
          <ul className="space-y-2">
            {items.map((item) => (
              <li key={item.id} className="flex items-start gap-3 rounded-xl bg-white p-4 shadow-sm">
                <CheckButton onClick={() => markBought(item)} label="Mark as bought" />
                <div className="flex-1">
                  <p>{item.body}</p>
                  <p className="mt-1 text-xs text-stone-500">
                    For <Link to={`/r/${item.room_slug}`} className="text-teal-700 underline">{item.task_title}</Link> in{' '}
                    {item.room_name} · {item.author} · {formatDate(item.created_at)}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

function CheckButton({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full border-2 border-stone-300 text-transparent hover:border-teal-600 hover:text-teal-600"
      aria-label={label}
    >
      <Check className="size-4" />
    </button>
  )
}
