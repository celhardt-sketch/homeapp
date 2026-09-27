export type TaskStatus = 'ok' | 'due' | 'overdue'

export interface Note {
  id: number
  task_id: number
  author: string
  body: string
  needs_purchase: boolean
  resolved: boolean
  created_at: string
}

export interface Task {
  id: number
  room_id: number
  title: string
  description: string
  frequency_days: number | null
  sort_order: number
  active: boolean
  last_completed_at: string | null
  last_completed_by: string | null
  status: TaskStatus
  notes: Note[]
}

export interface RoomSummary {
  id: number
  slug: string
  name: string
  icon: string
  sort_order: number
  task_count: number
  due_count: number
  note_count: number
}

export interface Room extends RoomSummary {
  tasks: Task[]
}

export interface Completion {
  id: number
  completed_by: string
  completed_at: string
}

export interface ShoppingItem extends Note {
  task_title: string
  room_name: string
  room_slug: string
}

export interface ActivityItem extends Completion {
  task_title: string
  room_name: string
  room_slug: string
}

const TOKEN_KEY = 'hm:adminToken'

export function getAdminToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setAdminToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const token = getAdminToken()
  const res = await fetch(url, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const api = {
  adminLogin: (password: string) =>
    request<{ token: string }>('/api/admin/login', { method: 'POST', body: JSON.stringify({ password }) }),
  adminMe: () => request<{ ok: boolean }>('/api/admin/me'),
  adminChangePassword: (current_password: string, new_password: string) =>
    request<{ token: string }>('/api/admin/password', {
      method: 'POST',
      body: JSON.stringify({ current_password, new_password }),
    }),

  rooms: () => request<RoomSummary[]>('/api/rooms'),
  room: (slug: string) => request<Room>(`/api/rooms/${encodeURIComponent(slug)}`),
  createRoom: (body: { name: string; slug?: string; icon?: string }) =>
    request<Room>('/api/rooms', { method: 'POST', body: JSON.stringify(body) }),
  updateRoom: (id: number, body: Partial<Pick<RoomSummary, 'name' | 'slug' | 'icon'>>) =>
    request<Room>(`/api/rooms/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteRoom: (id: number) => request<void>(`/api/rooms/${id}`, { method: 'DELETE' }),

  createTask: (body: { room_id: number; title: string; description?: string; frequency_days?: number | null }) =>
    request<Task>('/api/tasks', { method: 'POST', body: JSON.stringify(body) }),
  updateTask: (id: number, body: Partial<Pick<Task, 'title' | 'description' | 'frequency_days' | 'active'>>) =>
    request<Task>(`/api/tasks/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteTask: (id: number) => request<void>(`/api/tasks/${id}`, { method: 'DELETE' }),
  completeTask: (id: number, completed_by: string) =>
    request<Task>(`/api/tasks/${id}/complete`, { method: 'POST', body: JSON.stringify({ completed_by }) }),
  undoCompletion: (completionId: number) =>
    request<void>(`/api/completions/${completionId}`, { method: 'DELETE' }),
  history: (taskId: number) => request<Completion[]>(`/api/tasks/${taskId}/history`),

  addNote: (taskId: number, body: { author: string; body: string; needs_purchase: boolean }) =>
    request<Note>(`/api/tasks/${taskId}/notes`, { method: 'POST', body: JSON.stringify(body) }),
  resolveNote: (id: number, resolved = true) =>
    request<Note>(`/api/notes/${id}`, { method: 'PATCH', body: JSON.stringify({ resolved }) }),

  shopping: () => request<ShoppingItem[]>('/api/shopping'),
  activity: () => request<ActivityItem[]>('/api/activity'),
}

export function formatDate(iso: string | null): string {
  if (!iso) return 'Never'
  const d = new Date(iso)
  const diffDays = Math.floor((Date.now() - d.getTime()) / 86400000)
  if (diffDays === 0) return `Today, ${d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`
  if (diffDays === 1) return 'Yesterday'
  if (diffDays < 7) return `${diffDays} days ago`
  return d.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' })
}

export function formatFrequency(days: number | null): string {
  if (!days) return 'As needed'
  if (days === 1) return 'Daily'
  if (days === 7) return 'Weekly'
  if (days === 14) return 'Every 2 weeks'
  if (days === 30) return 'Monthly'
  if (days === 90) return 'Every 3 months'
  if (days === 180) return 'Every 6 months'
  if (days === 365) return 'Yearly'
  return `Every ${days} days`
}
