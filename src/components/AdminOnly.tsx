import { ShieldAlert } from 'lucide-react'

export default function AdminOnly({ what }: { what: string }) {
  return (
    <div className="mx-auto mt-10 max-w-sm space-y-2 rounded-2xl bg-white p-6 text-center shadow-sm">
      <ShieldAlert className="mx-auto size-8 text-amber-600" />
      <h1 className="text-lg font-semibold">Admin only</h1>
      <p className="text-sm text-stone-500">{what} is only available with the admin password. Ask Mom.</p>
    </div>
  )
}
