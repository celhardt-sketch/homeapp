import { useEffect, useState } from 'react'
import { Lock } from 'lucide-react'
import { api } from '../api'

const NAME_KEY = 'hm:lastLoginName'

export default function LoginForm({ onLogin }: { onLogin: (name: string, password: string) => Promise<void> }) {
  const [names, setNames] = useState<string[]>([])
  const [name, setName] = useState(() => localStorage.getItem(NAME_KEY) ?? '')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.loginNames().then(setNames).catch(() => setNames([]))
  }, [])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await onLogin(name.trim(), password)
      localStorage.setItem(NAME_KEY, name.trim())
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="mx-auto mt-10 max-w-sm space-y-4 rounded-2xl bg-white p-6 shadow-sm">
      <div className="flex items-center gap-2">
        <span className="flex size-10 items-center justify-center rounded-full bg-teal-50 text-teal-700">
          <Lock className="size-5" />
        </span>
        <div>
          <h1 className="text-lg font-semibold">Who's this?</h1>
          <p className="text-sm text-stone-500">Log in once on this device; what you check off is recorded under your name.</p>
        </div>
      </div>
      {names.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {names.map((n) => (
            <button
              key={n}
              type="button"
              onClick={() => setName(n)}
              className={`rounded-full px-3 py-1 text-sm ${
                n.toLowerCase() === name.trim().toLowerCase() ? 'bg-teal-700 text-white' : 'bg-stone-100 text-stone-700'
              }`}
            >
              {n}
            </button>
          ))}
        </div>
      )}
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Your first name"
        autoComplete="username"
        className="w-full rounded-lg border border-stone-300 px-3 py-2 outline-none focus:border-teal-600"
      />
      <input
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        placeholder="Password"
        autoComplete="current-password"
        className="w-full rounded-lg border border-stone-300 px-3 py-2 outline-none focus:border-teal-600"
      />
      {error && <p className="rounded-lg bg-red-50 p-2 text-sm text-red-700">{error}</p>}
      <button
        type="submit"
        disabled={!name.trim() || !password || busy}
        className="w-full rounded-lg bg-teal-700 px-3 py-2 font-medium text-white disabled:opacity-40"
      >
        Log in
      </button>
    </form>
  )
}
