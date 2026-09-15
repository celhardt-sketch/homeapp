import { useState } from 'react'

export default function NamePrompt({ onSubmit }: { onSubmit: (name: string) => void }) {
  const [value, setValue] = useState('')
  return (
    <div className="fixed inset-0 z-20 flex items-end justify-center bg-black/40 p-4 sm:items-center">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (value.trim()) onSubmit(value)
        }}
        className="w-full max-w-sm rounded-2xl bg-white p-5 shadow-xl"
      >
        <h2 className="text-lg font-semibold">Who's this?</h2>
        <p className="mt-1 text-sm text-stone-500">
          Your name is saved on this device and recorded with every task you check off.
        </p>
        <input
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Your first name"
          className="mt-4 w-full rounded-lg border border-stone-300 px-3 py-2 text-base outline-none focus:border-teal-600"
        />
        <button
          type="submit"
          disabled={!value.trim()}
          className="mt-4 w-full rounded-lg bg-teal-700 py-2.5 font-medium text-white disabled:opacity-40"
        >
          Continue
        </button>
      </form>
    </div>
  )
}
