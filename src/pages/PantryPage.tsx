import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Check, ListPlus, Pencil, Plus, Trash2, X } from 'lucide-react'
import { api, formatDate, type PantryCreated, type PantryItem } from '../api'

const CATEGORIES = ['Dry goods', 'Canned', 'Baking', 'Snacks', 'Spices', 'Drinks', 'Cleaning', 'Paper goods', 'Other']

export default function PantryPage({ userName }: { userName: string }) {
  const [items, setItems] = useState<PantryItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<PantryItem | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [showBulk, setShowBulk] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [filter, setFilter] = useState('')

  useEffect(() => {
    api.pantry().then(setItems).catch((e: Error) => setError(e.message))
  }, [])

  const grouped = useMemo(() => {
    const q = filter.trim().toLowerCase()
    const visible = (items ?? []).filter((i) => !q || i.name.toLowerCase().includes(q))
    const map = new Map<string, PantryItem[]>()
    for (const i of visible) {
      const key = i.category || 'Other'
      map.set(key, [...(map.get(key) ?? []), i])
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b))
  }, [items, filter])

  const lowCount = items?.filter((i) => i.low).length ?? 0

  function upsert(item: PantryItem) {
    setItems((prev) => {
      if (!prev) return [item]
      const exists = prev.some((i) => i.id === item.id)
      return exists ? prev.map((i) => (i.id === item.id ? item : i)) : [...prev, item]
    })
  }

  function absorbCreated(created: PantryCreated[]) {
    const dupes = created.filter((c) => c.duplicate)
    created.forEach(upsert)
    setNotice(
      dupes.length
        ? `Already in pantry, not added again: ${dupes.map((d) => `"${d.requested_name}" → ${d.name}`).join(', ')}`
        : null,
    )
  }

  async function toggleLow(item: PantryItem) {
    try {
      upsert(await api.updatePantryItem(item.id, { low: !item.low_flag, updated_by: userName }))
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function remove(item: PantryItem) {
    if (!confirm(`Remove "${item.name}" from the pantry list?`)) return
    try {
      await api.deletePantryItem(item.id)
      setItems((prev) => prev?.filter((i) => i.id !== item.id) ?? null)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  if (!items) return <p className="text-stone-500">Loading…</p>

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Pantry</h1>
          <p className="text-sm text-stone-500">
            {items.length} item{items.length === 1 ? '' : 's'}
            {lowCount > 0 && <> · <span className="text-amber-700">{lowCount} running low</span></>}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => {
              setEditing(null)
              setShowForm(false)
              setShowBulk((v) => !v)
            }}
            className="flex items-center gap-1 rounded-lg border border-teal-700 px-3 py-2 text-sm font-medium text-teal-800"
            title="Add several items at once"
          >
            <ListPlus className="size-4" /> Add many
          </button>
          <button
            onClick={() => {
              setEditing(null)
              setShowBulk(false)
              setShowForm(true)
            }}
            className="flex items-center gap-1 rounded-lg bg-teal-700 px-3 py-2 text-sm font-medium text-white"
          >
            <Plus className="size-4" /> Add
          </button>
        </div>
      </div>

      {error && <p className="rounded-lg bg-red-50 p-2 text-sm text-red-700">{error}</p>}
      {notice && (
        <p className="flex items-start justify-between gap-2 rounded-lg bg-amber-50 p-2 text-sm text-amber-800">
          <span>{notice}</span>
          <button onClick={() => setNotice(null)} aria-label="Dismiss" className="shrink-0">
            <X className="size-4" />
          </button>
        </p>
      )}

      {showBulk && (
        <BulkForm
          userName={userName}
          onSaved={(created) => {
            absorbCreated(created)
            setShowBulk(false)
          }}
          onCancel={() => setShowBulk(false)}
          onError={setError}
        />
      )}

      {(showForm || editing) && (
        <ItemForm
          item={editing}
          userName={userName}
          onSaved={(it) => {
            if ('duplicate' in it) absorbCreated([it])
            else upsert(it)
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

      {items.length > 5 && (
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Search pantry…"
          className="w-full rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm outline-none focus:border-teal-600"
        />
      )}

      {items.length === 0 && !showForm && (
        <p className="rounded-xl bg-white p-6 text-center text-sm text-stone-500 shadow-sm">
          Nothing tracked yet. Add what you keep stocked and mark items "low" when they run out — they'll show up on the To Buy page.
        </p>
      )}

      {grouped.map(([category, list]) => (
        <section key={category}>
          <h2 className="mb-1 px-1 text-xs font-semibold uppercase tracking-wide text-stone-500">{category}</h2>
          <ul className="divide-y divide-stone-100 overflow-hidden rounded-xl bg-white shadow-sm">
            {list.map((item) => (
              <li key={item.id} className="flex items-center gap-3 p-3">
                <button
                  onClick={() => toggleLow(item)}
                  className={`flex size-8 shrink-0 items-center justify-center rounded-full border-2 ${
                    item.low
                      ? 'border-amber-500 bg-amber-50 text-amber-700'
                      : 'border-stone-300 text-transparent hover:border-teal-600 hover:text-teal-600'
                  }`}
                  title={item.low_flag ? 'Mark as stocked' : 'Mark as running low'}
                  aria-label={item.low_flag ? 'Mark as stocked' : 'Mark as running low'}
                >
                  {item.low ? <AlertTriangle className="size-4" /> : <Check className="size-4" />}
                </button>
                <div className="min-w-0 flex-1">
                  <p className={`flex items-center gap-2 font-medium ${item.low ? 'text-amber-800' : ''}`}>
                    <span className="truncate">{item.name}</span>
                    {item.below_par && (
                      <span className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-semibold text-amber-800">
                        Below par ({item.par_level})
                      </span>
                    )}
                    {item.days_to_expiry !== null && item.days_to_expiry <= 7 && (
                      <span className="shrink-0 rounded-full bg-red-100 px-2 py-0.5 text-[11px] font-semibold text-red-800">
                        {item.days_to_expiry < 0 ? 'Expired' : item.days_to_expiry === 0 ? 'Expires today' : `Expires in ${item.days_to_expiry}d`}
                      </span>
                    )}
                  </p>
                  <p className="truncate text-xs text-stone-500">
                    {item.quantity && <>{item.quantity} · </>}
                    {item.low ? 'Low' : 'Stocked'}
                    {item.par_level !== null && !item.below_par && <> · par {item.par_level}</>}
                    {item.updated_by && <> · {item.updated_by}, {formatDate(item.updated_at)}</>}
                  </p>
                </div>
                <button onClick={() => setEditing(item)} className="p-1.5 text-stone-500" aria-label="Edit item">
                  <Pencil className="size-4" />
                </button>
                <button onClick={() => remove(item)} className="p-1.5 text-red-600" aria-label="Delete item">
                  <Trash2 className="size-4" />
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}

function ItemForm({
  item,
  userName,
  onSaved,
  onCancel,
  onError,
}: {
  item: PantryItem | null
  userName: string
  onSaved: (item: PantryItem | PantryCreated) => void
  onCancel: () => void
  onError: (msg: string) => void
}) {
  const [name, setName] = useState(item?.name ?? '')
  const [category, setCategory] = useState(item?.category ?? 'Dry goods')
  const [quantity, setQuantity] = useState(item?.quantity ?? '')
  const [low, setLow] = useState(item?.low_flag ?? false)
  const [par, setPar] = useState(item?.par_level?.toString() ?? '')
  const [expires, setExpires] = useState(item?.expires_on ?? '')

  useEffect(() => {
    setName(item?.name ?? '')
    setCategory(item?.category ?? 'Dry goods')
    setQuantity(item?.quantity ?? '')
    setLow(item?.low_flag ?? false)
    setPar(item?.par_level?.toString() ?? '')
    setExpires(item?.expires_on ?? '')
  }, [item])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    const body = {
      name,
      category,
      quantity,
      low,
      par_level: par.trim() === '' ? null : Number(par),
      expires_on: expires || null,
      updated_by: userName,
    }
    try {
      const saved = item ? await api.updatePantryItem(item.id, body) : await api.createPantryItem(body)
      onSaved(saved)
    } catch (err) {
      onError((err as Error).message)
    }
  }

  const inputCls = 'w-full rounded-lg border border-stone-300 px-3 py-2 text-sm outline-none focus:border-teal-600'
  return (
    <form onSubmit={submit} className="space-y-2 rounded-xl bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <h2 className="font-medium">{item ? 'Edit item' : 'Add pantry item'}</h2>
        <button type="button" onClick={onCancel} className="text-stone-500" aria-label="Cancel">
          <X className="size-4" />
        </button>
      </div>
      <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Item (e.g. Rice)" className={inputCls} />
      <div className="flex gap-2">
        <select value={category} onChange={(e) => setCategory(e.target.value)} className={`${inputCls} bg-white`}>
          {[...new Set([...CATEGORIES, category])].map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <input value={quantity} onChange={(e) => setQuantity(e.target.value)} placeholder="Qty (e.g. 2 bags)" className={inputCls} />
      </div>
      <div className="flex gap-2">
        <label className="flex-1 text-xs text-stone-500">
          Par level (low when qty ≤ this)
          <input
            type="number"
            min={0}
            step="any"
            inputMode="decimal"
            value={par}
            onChange={(e) => setPar(e.target.value)}
            placeholder="none"
            className={`${inputCls} mt-0.5`}
          />
        </label>
        <label className="flex-1 text-xs text-stone-500">
          Expires
          <input type="date" value={expires} onChange={(e) => setExpires(e.target.value)} className={`${inputCls} mt-0.5`} />
        </label>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={low} onChange={(e) => setLow(e.target.checked)} className="accent-amber-600" />
        Running low — add to To Buy
      </label>
      <button
        type="submit"
        disabled={!name.trim()}
        className="flex items-center gap-1 rounded-lg bg-teal-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
      >
        <Plus className="size-4" /> {item ? 'Save' : 'Add item'}
      </button>
    </form>
  )
}

/** One item per line: "Name", "Name, qty" or "Name, qty, category". Sent as a single request. */
function BulkForm({
  userName,
  onSaved,
  onCancel,
  onError,
}: {
  userName: string
  onSaved: (created: PantryCreated[]) => void
  onCancel: () => void
  onError: (msg: string) => void
}) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)

  const rows = text
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => {
      const [name, quantity = '', category = ''] = l.split(',').map((s) => s.trim())
      return { name, quantity, category, updated_by: userName }
    })
    .filter((r) => r.name)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!rows.length) return
    setBusy(true)
    try {
      onSaved(await api.createPantryItems(rows))
    } catch (err) {
      onError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="space-y-2 rounded-xl bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <h2 className="font-medium">Add several items</h2>
        <button type="button" onClick={onCancel} className="text-stone-500" aria-label="Cancel">
          <X className="size-4" />
        </button>
      </div>
      <textarea
        autoFocus
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={6}
        placeholder={'One per line, e.g.\nEggs, 12\nWhole milk, 1 gal, Drinks\nPaper towels'}
        className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm outline-none focus:border-teal-600"
      />
      <p className="text-xs text-stone-500">Name, then optional quantity and category. Items already in the pantry are skipped.</p>
      <button
        type="submit"
        disabled={!rows.length || busy}
        className="flex items-center gap-1 rounded-lg bg-teal-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
      >
        <ListPlus className="size-4" /> Add {rows.length || ''} item{rows.length === 1 ? '' : 's'}
      </button>
    </form>
  )
}
