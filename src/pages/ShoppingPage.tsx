import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Check } from 'lucide-react'
import { api, formatDate, type PantryItem, type ShoppingItem } from '../api'

export default function ShoppingPage({ userName }: { userName: string }) {
  const [items, setItems] = useState<ShoppingItem[] | null>(null)
  const [pantry, setPantry] = useState<PantryItem[] | null>(null)

  useEffect(() => {
    api.shopping().then(setItems).catch(() => setItems([]))
    api.pantry().then((all) => setPantry(all.filter((p) => p.low))).catch(() => setPantry([]))
  }, [])

  async function markBought(item: ShoppingItem) {
    await api.resolveNote(item.id)
    setItems((prev) => prev?.filter((i) => i.id !== item.id) ?? null)
  }

  async function markStocked(item: PantryItem) {
    await api.updatePantryItem(item.id, { low: false, updated_by: userName })
    setPantry((prev) => prev?.filter((i) => i.id !== item.id) ?? null)
  }

  if (!items || !pantry) return <p className="text-stone-500">Loading…</p>

  const empty = items.length === 0 && pantry.length === 0

  return (
    <div className="space-y-6">
      <div>
        <h1 className="mb-1 text-xl font-semibold">To Buy</h1>
        <p className="text-sm text-stone-500">Things flagged for a task plus pantry items running low.</p>
      </div>
      {empty && <p className="text-stone-500">Nothing on the list.</p>}

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
