import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Archive, ArchiveRestore, Bot, Copy, KeyRound, LogOut, Mail, Nfc, Pencil, Plus, Trash2, Users, X } from 'lucide-react'
import {
  api,
  formatDay,
  formatFrequency,
  setAdminToken,
  type ConnectorStatus,
  type ReminderSettings,
  type Room,
  type RoomSummary,
  type Task,
  type User,
  type UserRole,
} from '../api'
import { ROOM_ICONS, RoomIcon } from '../icons'
import type { Session } from '../useAdminAuth'

const FREQUENCIES: { label: string; value: number | null }[] = [
  { label: 'As needed', value: null },
  { label: 'Daily', value: 1 },
  { label: 'Weekly', value: 7 },
  { label: 'Every 2 weeks', value: 14 },
  { label: 'Monthly', value: 30 },
  { label: 'Every 3 months', value: 90 },
  { label: 'Every 6 months', value: 180 },
  { label: 'Yearly', value: 365 },
]

export default function AdminPage({ session, onLogout }: { session: Session; onLogout: () => void }) {
  const isAdmin = session.role === 'admin'
  const [rooms, setRooms] = useState<RoomSummary[]>([])
  const [showArchived, setShowArchived] = useState(false)
  const [selected, setSelected] = useState<Room | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showPassword, setShowPassword] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [users, setUsers] = useState<User[]>([])

  const loadRooms = useCallback(() => {
    api.rooms(showArchived).then(setRooms).catch((e: Error) => setError(e.message))
  }, [showArchived])
  useEffect(loadRooms, [loadRooms])
  const loadUsers = useCallback(() => {
    api.users().then(setUsers).catch(() => setUsers([]))
  }, [])
  useEffect(loadUsers, [loadUsers])

  async function openRoom(slug: string) {
    setSelected(await api.room(slug))
  }

  async function withError(fn: () => Promise<void>) {
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Manage</h1>
          <p className="text-sm text-stone-500">
            Each room has a tag link. Write that link to an NFC tag and stick it in the room — scanning it opens
            the room's task list.
          </p>
        </div>
        <div className="flex shrink-0 gap-1">
          <button
            onClick={() => setShowPassword((v) => !v)}
            className="rounded-lg p-2 text-stone-500 hover:bg-stone-200"
            title="Change my password"
            aria-label="Change my password"
          >
            <KeyRound className="size-4" />
          </button>
          <button
            onClick={onLogout}
            className="rounded-lg p-2 text-stone-500 hover:bg-stone-200"
            title="Log out"
            aria-label="Log out"
          >
            <LogOut className="size-4" />
          </button>
        </div>
      </div>

      {error && <p className="rounded-lg bg-red-50 p-2 text-sm text-red-700">{error}</p>}
      {notice && <p className="rounded-lg bg-amber-50 p-2 text-sm text-amber-800">{notice}</p>}

      {!isAdmin && (
        <p className="rounded-lg bg-amber-50 p-2 text-sm text-amber-800">
          You're logged in as {session.user?.name}. You can edit tasks; adding, renaming or archiving rooms, people and
          reminder settings are for admins — ask Mom.
        </p>
      )}

      {showPassword && <ChangePasswordForm onDone={() => setShowPassword(false)} />}

      {!selected && isAdmin && <UsersCard me={session.user} users={users} onChange={loadUsers} />}
      {!selected && isAdmin && <ReminderSettingsCard />}
      {!selected && isAdmin && <ConnectorCard />}

      {!selected ? (
        <>
          <ul className="space-y-2">
            {rooms.map((r) => (
              <li key={r.id} className={`flex items-center gap-3 rounded-xl bg-white p-3 shadow-sm ${r.active ? '' : 'opacity-60'}`}>
                <span className="flex size-9 items-center justify-center rounded-lg bg-teal-50 text-teal-700">
                  <RoomIcon name={r.icon} className="size-5" />
                </span>
                <button onClick={() => openRoom(r.slug)} className="flex-1 text-left">
                  <span className="block font-medium">
                    {r.name}
                    {!r.active && <span className="ml-2 rounded-full bg-stone-200 px-2 py-0.5 text-xs font-normal text-stone-600">archived</span>}
                  </span>
                  <span className="block text-xs text-stone-500">
                    {r.task_count} task{r.task_count === 1 ? '' : 's'} · tag id: {r.slug}
                  </span>
                </button>
                <button onClick={() => openRoom(r.slug)} className="p-2 text-stone-500" aria-label="Edit room">
                  <Pencil className="size-4" />
                </button>
              </li>
            ))}
          </ul>
          {isAdmin && (
            <label className="flex items-center gap-2 text-sm text-stone-500">
              <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
              Show archived rooms
            </label>
          )}
          {isAdmin && (
            <NewRoomForm
              onCreate={(body) =>
                withError(async () => {
                  setNotice(null)
                  const room = await api.createRoom(body)
                  if (room.duplicate) setNotice(`"${body.name}" looks like the existing room "${room.name}", so nothing was added.`)
                  loadRooms()
                  setSelected(room)
                })
              }
            />
          )}
        </>
      ) : (
        <RoomEditor
          room={selected}
          isAdmin={isAdmin}
          users={users}
          onBack={() => {
            setSelected(null)
            loadRooms()
          }}
          onChange={setSelected}
          onDelete={
            isAdmin
              ? () =>
                  withError(async () => {
                    await api.deleteRoom(selected.id)
                    setSelected(null)
                    loadRooms()
                  })
              : undefined
          }
          onError={setError}
        />
      )}
    </div>
  )
}

function ConnectorCard() {
  const [status, setStatus] = useState<ConnectorStatus | null>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [copied, setCopied] = useState(false)

  const load = useCallback(() => {
    api
      .connectorStatus()
      .then(setStatus)
      .catch((e: Error) => setMsg({ ok: false, text: e.message }))
  }, [])
  useEffect(load, [load])

  async function revoke() {
    if (!confirm('Disconnect Claude? Family devices and your admin login stay signed in.')) return
    setMsg(null)
    try {
      const r = await api.revokeConnector()
      setMsg({ ok: true, text: `Disconnected (${r.revoked_tokens} tokens revoked). Reconnect from Claude any time.` })
      load()
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message })
    }
  }

  function copyUrl() {
    if (!status) return
    navigator.clipboard.writeText(status.mcp_url).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  if (!status) return null

  return (
    <section className="rounded-xl bg-white p-4 shadow-sm">
      <h2 className="mb-1 flex items-center gap-2 font-medium">
        <Bot className="size-5 text-teal-700" /> Claude connector
      </h2>
      <p className="mb-2 text-sm text-stone-500">
        Add this URL as a custom connector in Claude; it will ask for your admin password once. Claude gets its own
        "connector" role: rooms, tasks, upkeep, pantry and shopping, never medications or settings.
      </p>
      <div className="mb-2 flex items-center gap-2">
        <code className="flex-1 truncate rounded-lg bg-stone-100 px-2 py-1.5 text-xs">{status.mcp_url}</code>
        <button onClick={copyUrl} className="rounded-lg border border-stone-300 p-1.5 text-stone-600" aria-label="Copy MCP URL">
          <Copy className="size-4" />
        </button>
        {copied && <span className="text-xs text-teal-700">Copied</span>}
      </div>
      {status.connected ? (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="rounded-full bg-green-50 px-2 py-0.5 text-xs text-green-700">
            Connected: {status.clients.map((c) => c.client_name).join(', ')}
          </span>
          {status.last_used_at && <span className="text-xs text-stone-500">Last used {formatDay(status.last_used_at)}</span>}
          <button onClick={revoke} className="ml-auto rounded-lg border border-red-200 px-3 py-1 text-sm text-red-700">
            Disconnect Claude
          </button>
        </div>
      ) : (
        <p className="text-sm text-stone-500">Not connected yet.</p>
      )}
      {msg && (
        <p className={`mt-2 rounded-lg p-2 text-sm ${msg.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>{msg.text}</p>
      )}
    </section>
  )
}

function ReminderSettingsCard() {
  const [settings, setSettings] = useState<ReminderSettings | null>(null)
  const [email, setEmail] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    api
      .reminderSettings()
      .then((s) => {
        setSettings(s)
        setEmail(s.reminder_email)
      })
      .catch((e: Error) => setMsg({ ok: false, text: e.message }))
  }, [])

  async function save() {
    setMsg(null)
    try {
      setSettings(await api.saveReminderSettings(email))
      setMsg({ ok: true, text: 'Saved.' })
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message })
    }
  }

  async function sendTest() {
    setMsg(null)
    try {
      if (settings && email !== settings.reminder_email) setSettings(await api.saveReminderSettings(email))
      await api.sendTestReminder()
      setMsg({ ok: true, text: `Test email sent to ${email}.` })
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message })
    }
  }

  if (!settings) return null

  return (
    <section className="rounded-xl bg-white p-4 shadow-sm">
      <h2 className="mb-1 flex items-center gap-2 font-medium">
        <Mail className="size-5 text-teal-700" /> Medication reorder reminders
      </h2>
      <p className="mb-2 text-sm text-stone-500">
        When a prescription hits its reorder date, an email goes to this address (checked hourly).
      </p>
      {!settings.email_configured && (
        <p className="mb-2 rounded-lg bg-amber-50 p-2 text-xs text-amber-800">
          Email sending isn't configured on the server yet (set RESEND_API_KEY or SMTP_HOST). Reminders still show in the
          app.
        </p>
      )}
      <div className="flex gap-2">
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          className="flex-1 rounded-lg border border-stone-300 px-3 py-2 text-sm outline-none focus:border-teal-600"
        />
        <button
          onClick={save}
          disabled={email === settings.reminder_email}
          className="rounded-lg bg-teal-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
        >
          Save
        </button>
        <button
          onClick={sendTest}
          disabled={!email || !settings.email_configured}
          className="rounded-lg border border-teal-700 px-3 py-2 text-sm font-medium text-teal-700 disabled:opacity-40"
        >
          Send test
        </button>
      </div>
      {msg && (
        <p className={`mt-2 rounded-lg p-2 text-sm ${msg.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
          {msg.text}
        </p>
      )}
      {settings.due.length > 0 && (
        <p className="mt-2 text-xs text-stone-500">
          Meds due now: {settings.due.map((d) => `${d.name} (${d.person}, ${formatDay(d.reorder_on)})`).join(', ')}
        </p>
      )}
      {settings.due_upkeep.length > 0 && (
        <p className="mt-2 text-xs text-stone-500">
          Upkeep due now: {settings.due_upkeep.map((d) => `${d.name} (${formatDay(d.due_on)})`).join(', ')}
        </p>
      )}
    </section>
  )
}

function ChangePasswordForm({ onDone }: { onDone: () => void }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirmNext, setConfirmNext] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    if (next !== confirmNext) {
      setError('New passwords do not match')
      return
    }
    try {
      const { token } = await api.changeOwnPassword(current, next)
      setAdminToken(token)
      setSaved(true)
      setTimeout(onDone, 1200)
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const inputCls = 'w-full rounded-lg border border-stone-300 px-3 py-2 text-sm outline-none focus:border-teal-600'
  return (
    <form onSubmit={submit} className="space-y-2 rounded-xl bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <h2 className="font-medium">Change my password</h2>
        <button type="button" onClick={onDone} className="text-stone-500" aria-label="Close">
          <X className="size-4" />
        </button>
      </div>
      <input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} placeholder="Current password" className={inputCls} />
      <input type="password" value={next} onChange={(e) => setNext(e.target.value)} placeholder="New password (min 4 characters)" className={inputCls} />
      <input type="password" value={confirmNext} onChange={(e) => setConfirmNext(e.target.value)} placeholder="Confirm new password" className={inputCls} />
      {error && <p className="rounded-lg bg-red-50 p-2 text-sm text-red-700">{error}</p>}
      {saved && <p className="rounded-lg bg-green-50 p-2 text-sm text-green-700">Password updated.</p>}
      <button
        type="submit"
        disabled={!current || next.length < 4 || !confirmNext}
        className="rounded-lg bg-teal-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
      >
        Update password
      </button>
    </form>
  )
}

function UsersCard({ me, users, onChange }: { me: User | null; users: User[]; onChange: () => void }) {
  const [name, setName] = useState('')
  const [role, setRole] = useState<UserRole>('member')
  const [password, setPassword] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const inputCls = 'rounded-lg border border-stone-300 px-3 py-2 text-sm outline-none focus:border-teal-600'

  async function run(fn: () => Promise<string>) {
    setMsg(null)
    try {
      setMsg({ ok: true, text: await fn() })
      onChange()
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message })
    }
  }

  return (
    <section className="rounded-xl bg-white p-4 shadow-sm">
      <h2 className="mb-1 flex items-center gap-2 font-medium">
        <Users className="size-5 text-teal-700" /> People
      </h2>
      <p className="mb-2 text-sm text-stone-500">Everyone logs in with their own name and password; admins can also manage rooms, people and medications.</p>
      <ul className="mb-3 divide-y divide-stone-100">
        {users.map((u) => (
          <li key={u.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
            <span className="font-medium">{u.name}</span>
            <span className="rounded-full bg-stone-100 px-2 py-0.5 text-xs text-stone-600">{u.role}</span>
            {u.id !== me?.id && (
              <span className="ml-auto flex gap-2 text-xs">
                <button
                  onClick={() => run(async () => ((await api.updateUser(u.id, { role: u.role === 'admin' ? 'member' : 'admin' })), `${u.name} is now ${u.role === 'admin' ? 'a member' : 'an admin'}.`))}
                  className="text-teal-700"
                >
                  Make {u.role === 'admin' ? 'member' : 'admin'}
                </button>
                <button
                  onClick={() => {
                    const next = prompt(`New password for ${u.name} (min 4 characters):`)
                    if (next && next.length >= 4) run(async () => ((await api.updateUser(u.id, { password: next })), `${u.name}'s password was reset; their devices will need to log in again.`))
                  }}
                  className="text-teal-700"
                >
                  Reset password
                </button>
                <button
                  onClick={() => {
                    if (confirm(`Remove ${u.name}? Their completed tasks stay in the history.`))
                      run(async () => ((await api.updateUser(u.id, { active: false })), `${u.name} can no longer log in.`))
                  }}
                  className="text-red-700"
                >
                  Remove
                </button>
              </span>
            )}
          </li>
        ))}
      </ul>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          run(async () => {
            const u = await api.createUser({ name: name.trim(), role, password })
            setName('')
            setPassword('')
            return `Added ${u.name}.`
          })
        }}
        className="flex flex-wrap gap-2"
      >
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="First name" className={`${inputCls} flex-1`} />
        <select value={role} onChange={(e) => setRole(e.target.value as UserRole)} className={`${inputCls} bg-white`}>
          <option value="member">member</option>
          <option value="admin">admin</option>
        </select>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          autoComplete="new-password"
          className={`${inputCls} flex-1`}
        />
        <button
          type="submit"
          disabled={!name.trim() || password.length < 4}
          className="flex items-center gap-1 rounded-lg bg-teal-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
        >
          <Plus className="size-4" /> Add person
        </button>
      </form>
      {msg && (
        <p className={`mt-2 rounded-lg p-2 text-sm ${msg.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>{msg.text}</p>
      )}
    </section>
  )
}

function NewRoomForm({ onCreate }: { onCreate: (body: { name: string; icon: string }) => void }) {
  const [name, setName] = useState('')
  const [icon, setIcon] = useState('home')
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        if (!name.trim()) return
        onCreate({ name, icon })
        setName('')
      }}
      className="rounded-xl bg-white p-4 shadow-sm"
    >
      <h2 className="mb-2 font-medium">Add a room</h2>
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Room name (e.g. Master Bath)"
        className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm outline-none focus:border-teal-600"
      />
      <IconPicker value={icon} onChange={setIcon} />
      <button
        type="submit"
        disabled={!name.trim()}
        className="mt-3 flex items-center gap-1 rounded-lg bg-teal-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
      >
        <Plus className="size-4" /> Add room
      </button>
    </form>
  )
}

function IconPicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {Object.entries(ROOM_ICONS).map(([key, { icon: Icon, label }]) => (
        <button
          type="button"
          key={key}
          title={label}
          onClick={() => onChange(key)}
          className={`flex size-9 items-center justify-center rounded-lg border ${
            value === key ? 'border-teal-600 bg-teal-50 text-teal-700' : 'border-stone-200 text-stone-500'
          }`}
        >
          <Icon className="size-4" />
        </button>
      ))}
    </div>
  )
}

function RoomEditor({
  room,
  isAdmin,
  users,
  onBack,
  onChange,
  onDelete,
  onError,
}: {
  room: Room
  isAdmin: boolean
  users: User[]
  onBack: () => void
  onChange: (r: Room) => void
  onDelete?: () => void
  onError: (msg: string) => void
}) {
  const [name, setName] = useState(room.name)
  const [slug, setSlug] = useState(room.slug)
  const [icon, setIcon] = useState(room.icon)
  const [copied, setCopied] = useState(false)
  const tagUrl = `${window.location.origin}/r/${room.slug}`

  async function save() {
    try {
      const body = slug !== room.slug ? { name, slug, icon } : { name, icon }
      const updated = await api.updateRoom(room.id, body)
      setSlug(updated.slug)
      onChange(updated)
    } catch (e) {
      onError((e as Error).message)
    }
  }

  async function setActive(active: boolean) {
    try {
      onChange(await api.updateRoom(room.id, { active }))
    } catch (e) {
      onError((e as Error).message)
    }
  }

  async function copy() {
    await navigator.clipboard.writeText(tagUrl)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  const dirty = name !== room.name || slug !== room.slug || icon !== room.icon

  return (
    <div className="space-y-4">
      <button onClick={onBack} className="text-sm text-teal-700">
        ← All rooms
      </button>

      {isAdmin && (
        <section className="rounded-xl bg-white p-4 shadow-sm">
          <h2 className="mb-3 font-medium">
            Room details
            {!room.active && <span className="ml-2 rounded-full bg-stone-200 px-2 py-0.5 text-xs font-normal text-stone-600">archived</span>}
          </h2>
          <label className="block text-xs font-medium text-stone-500">Name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="mt-1 w-full rounded-lg border border-stone-300 px-3 py-2 text-sm outline-none focus:border-teal-600"
          />
          <label className="mt-3 block text-xs font-medium text-stone-500">Tag id (used in the link)</label>
          <input
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            className="mt-1 w-full rounded-lg border border-stone-300 px-3 py-2 font-mono text-sm outline-none focus:border-teal-600"
          />
          <p className="mt-1 text-xs text-stone-500">Renaming keeps the old tag id working, so tags already on the wall don't break.</p>
          <IconPicker value={icon} onChange={setIcon} />
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <button
              onClick={save}
              disabled={!dirty || !name.trim() || !slug.trim()}
              className="rounded-lg bg-teal-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
            >
              Save
            </button>
            <button onClick={() => setActive(!room.active)} className="flex items-center gap-1 text-sm text-stone-600">
              {room.active ? (
                <>
                  <Archive className="size-4" /> Archive room
                </>
              ) : (
                <>
                  <ArchiveRestore className="size-4" /> Restore room
                </>
              )}
            </button>
            {onDelete && (
              <button
                onClick={() => {
                  if (confirm(`Delete "${room.name}"? This only works once it has no tasks; archive it to keep the history.`)) onDelete()
                }}
                className="ml-auto flex items-center gap-1 text-sm text-red-700"
              >
                <Trash2 className="size-4" /> Delete room
              </button>
            )}
          </div>
        </section>
      )}

      <section className="rounded-xl border border-teal-200 bg-teal-50 p-4">
        <h2 className="mb-1 flex items-center gap-2 font-medium text-teal-900">
          <Nfc className="size-5" /> NFC tag link
        </h2>
        <p className="mb-2 text-sm text-teal-800">
          Write this URL to the room's NFC tag (use an app like "NFC Tools" on iPhone or Android, choose
          "Write" → "URL"). Scanning the tag will open this room.
        </p>
        <div className="flex items-center gap-2">
          <code className="flex-1 truncate rounded-lg bg-white px-3 py-2 text-sm">{tagUrl}</code>
          <button onClick={copy} className="rounded-lg bg-white p-2 text-teal-800" aria-label="Copy link">
            <Copy className="size-4" />
          </button>
        </div>
        {copied && <p className="mt-1 text-xs text-teal-800">Copied!</p>}
        <Link to={`/r/${room.slug}`} className="mt-2 inline-block text-sm text-teal-800 underline">
          Open room page
        </Link>
      </section>

      <TaskEditor room={room} canAssign={isAdmin} users={users} onChange={onChange} onError={onError} />
    </div>
  )
}

function TaskEditor({
  room,
  canAssign,
  users,
  onChange,
  onError,
}: {
  room: Room
  canAssign: boolean
  users: User[]
  onChange: (r: Room) => void
  onError: (msg: string) => void
}) {
  const [editing, setEditing] = useState<Task | null>(null)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [frequency, setFrequency] = useState<number | null>(30)
  const [assignee, setAssignee] = useState<number | null>(null)

  function startEdit(t: Task) {
    setEditing(t)
    setTitle(t.title)
    setDescription(t.description)
    setFrequency(t.frequency_days)
    setAssignee(t.assignee_id)
  }

  function reset() {
    setEditing(null)
    setTitle('')
    setDescription('')
    setFrequency(30)
    setAssignee(null)
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!title.trim()) return
    try {
      if (editing) {
        const body = { title, description, frequency_days: frequency ?? 0, ...(canAssign && assignee !== editing.assignee_id ? { assignee_id: assignee } : {}) }
        const updated = await api.updateTask(editing.id, body)
        onChange({ ...room, tasks: room.tasks.map((t) => (t.id === updated.id ? updated : t)) })
      } else {
        const created = await api.createTask({ room_id: room.id, title, description, frequency_days: frequency, ...(canAssign ? { assignee_id: assignee } : {}) })
        onChange({ ...room, tasks: [...room.tasks, created] })
      }
      reset()
    } catch (err) {
      onError((err as Error).message)
    }
  }

  async function remove(t: Task) {
    if (!confirm(`Delete "${t.title}"?`)) return
    try {
      await api.deleteTask(t.id)
      onChange({ ...room, tasks: room.tasks.filter((x) => x.id !== t.id) })
      if (editing?.id === t.id) reset()
    } catch (err) {
      onError((err as Error).message)
    }
  }

  return (
    <section className="rounded-xl bg-white p-4 shadow-sm">
      <h2 className="mb-3 font-medium">Tasks</h2>
      <ul className="mb-4 divide-y divide-stone-100">
        {room.tasks.length === 0 && <li className="py-2 text-sm text-stone-400">No tasks yet.</li>}
        {room.tasks.map((t) => (
          <li key={t.id} className="flex items-center gap-2 py-2">
            <div className="flex-1">
              <p className="text-sm font-medium">{t.title}</p>
              <p className="text-xs text-stone-500">
                {formatFrequency(t.frequency_days)}
                {t.assignee && ` · ${t.assignee}`}
              </p>
            </div>
            <button onClick={() => startEdit(t)} className="p-1.5 text-stone-500" aria-label="Edit task">
              <Pencil className="size-4" />
            </button>
            <button onClick={() => remove(t)} className="p-1.5 text-red-600" aria-label="Delete task">
              <Trash2 className="size-4" />
            </button>
          </li>
        ))}
      </ul>

      <form onSubmit={submit} className="space-y-2 rounded-lg bg-stone-50 p-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium">{editing ? 'Edit task' : 'Add a task'}</h3>
          {editing && (
            <button type="button" onClick={reset} className="text-stone-500" aria-label="Cancel edit">
              <X className="size-4" />
            </button>
          )}
        </div>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Task (e.g. Change furnace filter)"
          className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm outline-none focus:border-teal-600"
        />
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Details (optional)"
          className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm outline-none focus:border-teal-600"
        />
        <select
          value={frequency ?? ''}
          onChange={(e) => setFrequency(e.target.value === '' ? null : Number(e.target.value))}
          className="w-full rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm outline-none focus:border-teal-600"
        >
          {FREQUENCIES.map((f) => (
            <option key={f.label} value={f.value ?? ''}>
              {f.label}
            </option>
          ))}
        </select>
        {canAssign && (
          <select
            value={assignee ?? ''}
            onChange={(e) => setAssignee(e.target.value === '' ? null : Number(e.target.value))}
            className="w-full rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm outline-none focus:border-teal-600"
          >
            <option value="">Nobody in particular</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>
                Assign to {u.name}
              </option>
            ))}
          </select>
        )}
        <button
          type="submit"
          disabled={!title.trim()}
          className="flex items-center gap-1 rounded-lg bg-teal-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
        >
          <Plus className="size-4" /> {editing ? 'Save task' : 'Add task'}
        </button>
      </form>
    </section>
  )
}
