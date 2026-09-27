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
  room_id: number | null
  room_name: string | null
  room_slug: string | null
  title: string
  description: string
  frequency_days: number | null
  sort_order: number
  active: boolean
  assignee_id: number | null
  assignee: string | null
  due_on: string | null
  created_at: string
  last_completed_at: string | null
  last_completed_by: string | null
  status: TaskStatus
  done: boolean
  notes: Note[]
}

export interface RoomSummary {
  id: number
  slug: string
  name: string
  icon: string
  sort_order: number
  active: boolean
  task_count: number
  due_count: number
  note_count: number
}

export interface Room extends RoomSummary {
  tasks: Task[]
}

/** POST /api/rooms result: `duplicate` means an existing room was returned and nothing was created. */
export type RoomCreated = Room & { duplicate: boolean; duplicate_of?: string }

export interface Completion {
  id: number
  completed_by: string
  completed_at: string
  user_id: number | null
}

export interface User {
  id: number
  name: string
  role: UserRole
  active: boolean
  email: string
}

export interface ShoppingItem {
  id: number
  name: string
  notes: string
  assignee_id: number | null
  assignee: string | null
  due_on: string | null
  added_by: string
  created_at: string
  bought_at: string | null
  bought_by: string | null
  done: boolean
  overdue: boolean
}

export interface ListItem {
  kind: 'task' | 'shopping'
  id: number
  title: string
  notes: string
  room_name: string | null
  room_slug: string | null
  due_on: string | null
  frequency_days: number | null
  status: TaskStatus
  overdue: boolean
  done: boolean
  created_at: string
  last_completed_at: string | null
  last_completed_by: string | null
}

export interface PersonList {
  user: User
  items: ListItem[]
  overdue_count: number
}

export interface Notification {
  id: number
  user_id: number
  kind: 'task' | 'shopping'
  ref_id: number
  message: string
  created_by: string
  created_at: string
  read_at: string | null
}

export interface PurchaseNote extends Note {
  task_title: string
  room_name: string
  room_slug: string
}

export interface ActivityItem extends Completion {
  task_title: string
  room_name: string
  room_slug: string
}

export interface PantryItem {
  id: number
  name: string
  category: string
  quantity: string
  /** true when flagged by hand OR quantity <= par_level */
  low: boolean
  low_flag: boolean
  par_level: number | null
  below_par: boolean
  expires_on: string | null
  days_to_expiry: number | null
  updated_by: string
  updated_at: string
}

export interface PantryItemInput {
  name: string
  category?: string
  quantity?: string
  low?: boolean
  par_level?: number | null
  expires_on?: string | null
  updated_by: string
}

/** POST /api/pantry result: `duplicate` means an existing row was returned and nothing was inserted. */
export type PantryCreated = PantryItem & { duplicate: boolean; requested_name?: string }

export type MedStatus = 'none' | 'ok' | 'soon' | 'due'

export interface Child {
  id: number
  name: string
  active: boolean
}

/** Derived from the latest pickup on every read; `called_waiting` is the manual "I called, waiting" state. */
export type RefillStatus = 'no_pickup' | 'ok' | 'refill_due' | 'called_waiting' | 'urgent'

export interface Prescription {
  id: number
  child_id: number
  child: string
  name: string
  pharmacy: string
  contact_name: string
  contact_phone: string
  days_supply: number
  refill_after_days: number
  active: boolean
  notes: string
  assignee_id: number | null
  assignee: string | null
  last_picked_up_on: string | null
  last_picked_up_by: string | null
  days_since_pickup: number | null
  days_of_supply_left: number | null
  refill_status: RefillStatus
  called_on: string | null
  called_by: string
  called_notes: string
}

export interface PrescriptionInput {
  child_id: number
  name: string
  pharmacy?: string
  contact_name?: string
  contact_phone?: string
  days_supply?: number
  refill_after_days?: number
  notes?: string
  assignee_id?: number | null
}

export interface Pickup {
  id: number
  prescription_id: number
  picked_up_on: string
  picked_up_by: string
  user_id: number | null
  notes: string
}

export interface UpkeepItem {
  id: number
  name: string
  category: string
  interval_days: number
  notes: string
  active: boolean
  last_done_on: string | null
  last_done_by: string | null
  due_on: string | null
  days_left: number | null
  status: MedStatus
}

export interface DeclutterSpot {
  id: number
  name: string
  room_id: number | null
  room: string | null
  notes: string
  done: boolean
  done_on: string | null
  done_by: string | null
  days_since_done: number | null
  created_at: string
  created_by: string
  duplicate?: boolean
}

export interface UpkeepLog {
  id: number
  item_id: number
  done_on: string
  done_by: string
  note: string
  reminder_sent_at: string | null
}

export interface ConnectorStatus {
  mcp_url: string
  connected: boolean
  clients: { client_id: string; client_name: string; created_at: string }[]
  active_tokens: number
  last_used_at: string | null
}

export interface ReminderSettings {
  reminder_email: string
  email_configured: boolean
  refill_detail_in_notifications: boolean
  due_refills: Prescription[]
  due_upkeep: { item_id: number; name: string; category: string; due_on: string }[]
}

export type UserRole = 'admin' | 'member'
export type Role = UserRole | 'connector'

const TOKEN_KEY = 'hm:adminToken'
export const SESSION_EXPIRED_EVENT = 'hm:session-expired'
// 401 from these means "wrong password typed", not an expired session.
const PASSWORD_ROUTES = new Set(['/api/login', '/api/me/password'])

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

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
    if (res.status === 401 && token && !PASSWORD_ROUTES.has(url)) {
      setAdminToken(null)
      window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT))
    }
    let detail = res.statusText
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      /* ignore */
    }
    throw new ApiError(detail, res.status)
  }
  // Member sessions slide: the server hands back a renewed token on every request.
  const renewed = res.headers.get('X-Session-Token')
  if (renewed) setAdminToken(renewed)
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const api = {
  loginNames: () => request<string[]>('/api/login/names'),
  login: (name: string, password: string) =>
    request<{ token: string; role: Role; user: User }>('/api/login', { method: 'POST', body: JSON.stringify({ name, password }) }),
  session: () => request<{ role: Role; user: User | null }>('/api/session'),
  changeOwnPassword: (current_password: string, new_password: string) =>
    request<{ token: string }>('/api/me/password', {
      method: 'POST',
      body: JSON.stringify({ current_password, new_password }),
    }),

  users: (includeInactive = false) => request<User[]>(`/api/users${includeInactive ? '?include_inactive=true' : ''}`),
  createUser: (body: { name: string; role: UserRole; password: string; email?: string }) =>
    request<User>('/api/admin/users', { method: 'POST', body: JSON.stringify(body) }),
  updateUser: (id: number, body: Partial<Pick<User, 'name' | 'role' | 'email' | 'active'>> & { password?: string }) =>
    request<User>(`/api/admin/users/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteUser: (id: number) => request<void>(`/api/admin/users/${id}`, { method: 'DELETE' }),

  myList: (includeDone = false) => request<PersonList>(`/api/me/list${includeDone ? '?include_done=true' : ''}`),
  userList: (id: number, includeDone = false) =>
    request<PersonList>(`/api/users/${id}/list${includeDone ? '?include_done=true' : ''}`),
  notifications: () => request<Notification[]>('/api/me/notifications?limit=20'),
  markNotificationsRead: () => request<{ marked: number }>('/api/me/notifications/read', { method: 'POST' }),

  rooms: (includeArchived = false) => request<RoomSummary[]>(`/api/rooms${includeArchived ? '?include_archived=true' : ''}`),
  room: (slug: string) => request<Room>(`/api/rooms/${encodeURIComponent(slug)}`),
  createRoom: (body: { name: string; slug?: string; icon?: string }) =>
    request<RoomCreated>('/api/rooms', { method: 'POST', body: JSON.stringify(body) }),
  updateRoom: (id: number, body: Partial<Pick<RoomSummary, 'name' | 'slug' | 'icon' | 'active' | 'sort_order'>>) =>
    request<Room>(`/api/rooms/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteRoom: (id: number) => request<void>(`/api/rooms/${id}`, { method: 'DELETE' }),

  createTask: (body: {
    room_id?: number | null
    title: string
    description?: string
    frequency_days?: number | null
    assignee_id?: number | null
    due_on?: string | null
  }) => request<Task>('/api/tasks', { method: 'POST', body: JSON.stringify(body) }),
  updateTask: (id: number, body: Partial<Pick<Task, 'title' | 'description' | 'frequency_days' | 'active' | 'assignee_id' | 'due_on'>>) =>
    request<Task>(`/api/tasks/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteTask: (id: number) => request<void>(`/api/tasks/${id}`, { method: 'DELETE' }),
  completeTask: (id: number) => request<Task>(`/api/tasks/${id}/complete`, { method: 'POST', body: JSON.stringify({}) }),
  undoCompletion: (completionId: number) =>
    request<void>(`/api/completions/${completionId}`, { method: 'DELETE' }),
  history: (taskId: number) => request<Completion[]>(`/api/tasks/${taskId}/history`),

  addNote: (taskId: number, body: { author: string; body: string; needs_purchase: boolean }) =>
    request<Note>(`/api/tasks/${taskId}/notes`, { method: 'POST', body: JSON.stringify(body) }),
  resolveNote: (id: number, resolved = true) =>
    request<Note>(`/api/notes/${id}`, { method: 'PATCH', body: JSON.stringify({ resolved }) }),

  shopping: () => request<PurchaseNote[]>('/api/shopping'),
  shoppingItems: (includeDone = false) => request<ShoppingItem[]>(`/api/shopping-items${includeDone ? '?include_done=true' : ''}`),
  createShoppingItem: (body: { name: string; notes?: string; assignee_id?: number | null; due_on?: string | null }) =>
    request<ShoppingItem>('/api/shopping-items', { method: 'POST', body: JSON.stringify(body) }),
  updateShoppingItem: (id: number, body: Partial<Pick<ShoppingItem, 'name' | 'notes' | 'assignee_id' | 'due_on'>> & { bought?: boolean }) =>
    request<ShoppingItem>(`/api/shopping-items/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteShoppingItem: (id: number) => request<void>(`/api/shopping-items/${id}`, { method: 'DELETE' }),
  activity: () => request<ActivityItem[]>('/api/activity'),

  pantry: () => request<PantryItem[]>('/api/pantry'),
  createPantryItem: (body: PantryItemInput) =>
    request<PantryCreated>('/api/pantry', { method: 'POST', body: JSON.stringify(body) }),
  createPantryItems: (body: PantryItemInput[]) =>
    request<PantryCreated[]>('/api/pantry', { method: 'POST', body: JSON.stringify(body) }),
  updatePantryItem: (id: number, body: Partial<Omit<PantryItemInput, 'updated_by'>> & { updated_by: string }) =>
    request<PantryItem>(`/api/pantry/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deletePantryItem: (id: number) => request<void>(`/api/pantry/${id}`, { method: 'DELETE' }),

  children: (includeInactive = false) => request<Child[]>(`/api/children${includeInactive ? '?include_inactive=true' : ''}`),
  createChild: (name: string) =>
    request<Child & { duplicate: boolean }>('/api/children', { method: 'POST', body: JSON.stringify({ name }) }),
  updateChild: (id: number, body: Partial<Pick<Child, 'name' | 'active'>>) =>
    request<Child>(`/api/children/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  prescriptions: (includeInactive = false) =>
    request<Prescription[]>(`/api/prescriptions${includeInactive ? '?include_inactive=true' : ''}`),
  refillsDue: () => request<Prescription[]>('/api/refills'),
  createPrescription: (body: PrescriptionInput) =>
    request<Prescription & { duplicate: boolean }>('/api/prescriptions', { method: 'POST', body: JSON.stringify(body) }),
  updatePrescription: (id: number, body: Partial<PrescriptionInput> & { active?: boolean }) =>
    request<Prescription>(`/api/prescriptions/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  logPickup: (id: number, body: { picked_up_on: string; notes?: string; override?: boolean }) =>
    request<Prescription>(`/api/prescriptions/${id}/pickups`, { method: 'POST', body: JSON.stringify(body) }),
  pickupHistory: (id: number) => request<Pickup[]>(`/api/prescriptions/${id}/pickups`),
  markCalled: (id: number, notes = '') =>
    request<Prescription>(`/api/prescriptions/${id}/called`, { method: 'POST', body: JSON.stringify({ notes }) }),
  deletePickup: (id: number) => request<void>(`/api/pickups/${id}`, { method: 'DELETE' }),
  deletePrescription: (id: number) => request<void>(`/api/prescriptions/${id}`, { method: 'DELETE' }),
  deleteChild: (id: number) => request<void>(`/api/children/${id}`, { method: 'DELETE' }),
  deleteNote: (id: number) => request<void>(`/api/notes/${id}`, { method: 'DELETE' }),
  deleteNeed: (id: number) => request<void>(`/api/needs/${id}`, { method: 'DELETE' }),

  upkeep: () => request<UpkeepItem[]>('/api/upkeep'),
  createUpkeep: (body: { name: string; category?: string; interval_days: number; notes?: string; last_done_on?: string | null }) =>
    request<UpkeepItem>('/api/upkeep', { method: 'POST', body: JSON.stringify(body) }),
  updateUpkeep: (id: number, body: Partial<Pick<UpkeepItem, 'name' | 'category' | 'interval_days' | 'notes' | 'active'>>) =>
    request<UpkeepItem>(`/api/upkeep/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteUpkeep: (id: number) => request<void>(`/api/upkeep/${id}`, { method: 'DELETE' }),
  logUpkeep: (id: number, body: { done_on: string; done_by: string; note?: string }) =>
    request<UpkeepItem>(`/api/upkeep/${id}/logs`, { method: 'POST', body: JSON.stringify(body) }),
  declutter: (includeDone = false) => request<DeclutterSpot[]>(`/api/declutter${includeDone ? '?include_done=true' : ''}`),
  createDeclutter: (body: { name: string; room_id?: number | null; notes?: string }) =>
    request<DeclutterSpot>('/api/declutter', { method: 'POST', body: JSON.stringify(body) }),
  updateDeclutter: (id: number, body: { name?: string; room_id?: number | null; notes?: string; done?: boolean; done_on?: string }) =>
    request<DeclutterSpot>(`/api/declutter/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteDeclutter: (id: number) => request<void>(`/api/declutter/${id}`, { method: 'DELETE' }),
  upkeepHistory: (id: number) => request<UpkeepLog[]>(`/api/upkeep/${id}/logs`),
  deleteUpkeepLog: (id: number) => request<void>(`/api/upkeep-logs/${id}`, { method: 'DELETE' }),

  connectorStatus: () => request<ConnectorStatus>('/api/admin/connector'),
  revokeConnector: () => request<{ revoked_tokens: number }>('/api/admin/connector', { method: 'DELETE' }),
  reminderSettings: () => request<ReminderSettings>('/api/admin/reminders'),
  saveReminderSettings: (reminder_email: string, refill_detail_in_notifications?: boolean) =>
    request<ReminderSettings>('/api/admin/reminders', {
      method: 'PUT',
      body: JSON.stringify({ reminder_email, refill_detail_in_notifications }),
    }),
  sendTestReminder: () => request<{ ok: boolean }>('/api/admin/reminders/test', { method: 'POST' }),
}

export function formatDay(iso: string | null): string {
  if (!iso) return '—'
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' })
}

export function todayIso(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
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
