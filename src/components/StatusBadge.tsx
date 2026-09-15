import type { TaskStatus } from '../api'

const STYLES: Record<TaskStatus, string> = {
  ok: 'bg-emerald-100 text-emerald-800',
  due: 'bg-amber-100 text-amber-800',
  overdue: 'bg-red-100 text-red-800',
}

const LABELS: Record<TaskStatus, string> = { ok: 'Up to date', due: 'Due', overdue: 'Overdue' }

export default function StatusBadge({ status }: { status: TaskStatus }) {
  return (
    <span className={`whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${STYLES[status]}`}>{LABELS[status]}</span>
  )
}
