import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Check } from 'lucide-react'
import { api, formatDate, type ShoppingItem } from '../api'

export default function ShoppingPage() {
  const [items, setItems] = useState<ShoppingItem[] | null>(null)

  useEffect(() => {
    api.shopping().then(setItems).catch(() => setItems([]))
  }, [])

  async function markBought(item: ShoppingItem) {
    await api.resolveNote(item.id)
    setItems((prev) => prev?.filter((i) => i.id !== item.id) ?? null)
  }

  if (!items) return <p className="text-stone-500">Loading…</p>

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold">To Buy</h1>
      <p className="mb-4 text-sm text-stone-500">Items someone flagged as needed to finish a task.</p>
      {items.length === 0 && <p className="text-stone-500">Nothing on the list.</p>}
      <ul className="space-y-2">
        {items.map((item) => (
          <li key={item.id} className="flex items-start gap-3 rounded-xl bg-white p-4 shadow-sm">
            <button
              onClick={() => markBought(item)}
              className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full border-2 border-stone-300 text-transparent hover:border-teal-600 hover:text-teal-600"
              aria-label="Mark as bought"
            >
              <Check className="size-4" />
            </button>
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
    </div>
  )
}
