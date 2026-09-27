import { useState } from 'react'
import { Lock } from 'lucide-react'

export default function LoginForm({ onLogin }: { onLogin: (password: string) => Promise<void> }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await onLogin(password)
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
          <h1 className="text-lg font-semibold">Household login</h1>
          <p className="text-sm text-stone-500">Enter the house password once on this device to continue.</p>
        </div>
      </div>
      <input
        type="password"
        autoFocus
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        placeholder="Password"
        className="w-full rounded-lg border border-stone-300 px-3 py-2 outline-none focus:border-teal-600"
      />
      {error && <p className="rounded-lg bg-red-50 p-2 text-sm text-red-700">{error}</p>}
      <button
        type="submit"
        disabled={!password || busy}
        className="w-full rounded-lg bg-teal-700 px-3 py-2 font-medium text-white disabled:opacity-40"
      >
        Log in
      </button>
    </form>
  )
}
