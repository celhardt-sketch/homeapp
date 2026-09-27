import { useCallback, useEffect, useState } from 'react'
import { Link, NavLink, Route, Routes } from 'react-router-dom'
import { Bell, Home, ListChecks, Package, Pill, Settings, ShoppingCart, Wrench } from 'lucide-react'
import { api, formatDate, type Notification } from './api'
import { useAdminAuth } from './useAdminAuth'
import LoginForm from './components/LoginForm'
import HomePage from './pages/HomePage'
import RoomPage from './pages/RoomPage'
import AdminPage from './pages/AdminPage'
import ShoppingPage from './pages/ShoppingPage'
import PantryPage from './pages/PantryPage'
import RefillsPage from './pages/RefillsPage'
import UpkeepPage from './pages/UpkeepPage'
import MyListPage from './pages/MyListPage'

export default function App() {
  const { session, login, logout } = useAdminAuth()
  const loggedIn = session !== null && session !== undefined
  const isAdmin = session?.role === 'admin'
  const user = session?.user ?? null
  const name = user?.name ?? ''

  return (
    <div className="mx-auto flex min-h-screen max-w-xl flex-col">
      <header className="sticky top-0 z-10 flex items-center justify-between border-b border-stone-200 bg-white/90 px-4 py-3 backdrop-blur">
        <Link to="/" className="flex items-center gap-2 font-semibold text-teal-800">
          <Home className="size-5" /> Home Maintenance
        </Link>
        {user && <NotificationBell />}
      </header>

      <main className="flex-1 px-4 pb-24 pt-4">
        {session === undefined && <p className="text-center text-sm text-stone-500">Loading…</p>}
        {session === null && <LoginForm onLogin={login} />}
        {loggedIn && (
          <Routes>
            <Route path="/" element={user ? <MyListPage me={user} /> : <HomePage isAdmin={isAdmin} />} />
            <Route path="/rooms" element={<HomePage isAdmin={isAdmin} />} />
            <Route path="/r/:slug" element={<RoomPage userName={name} />} />
            <Route path="/shopping" element={<ShoppingPage userName={name} canAssign={isAdmin} myUserId={user?.id ?? null} />} />
            <Route path="/pantry" element={<PantryPage userName={name} />} />
            <Route path="/refills" element={<RefillsPage isAdmin={isAdmin} />} />
            <Route path="/meds" element={<RefillsPage isAdmin={isAdmin} />} />
            <Route path="/upkeep" element={<UpkeepPage userName={name} />} />
            <Route path="/admin" element={<AdminPage session={session} onLogout={logout} />} />
            <Route path="*" element={<p className="text-center text-stone-500">Page not found.</p>} />
          </Routes>
        )}
      </main>

      <nav className="fixed inset-x-0 bottom-0 z-10 border-t border-stone-200 bg-white pb-[env(safe-area-inset-bottom)]">
        <div className="mx-auto flex max-w-xl justify-around">
          <Tab to="/" icon={<ListChecks className="size-5" />} label="My list" />
          <Tab to="/rooms" icon={<Home className="size-5" />} label="Rooms" />
          <Tab to="/upkeep" icon={<Wrench className="size-5" />} label="Upkeep" />
          <Tab to="/pantry" icon={<Package className="size-5" />} label="Pantry" />
          <Tab to="/refills" icon={<Pill className="size-5" />} label="Refills" />
          <Tab to="/shopping" icon={<ShoppingCart className="size-5" />} label="To Buy" />
          <Tab to="/admin" icon={<Settings className="size-5" />} label="Manage" />
        </div>
      </nav>
    </div>
  )
}

function NotificationBell() {
  const [items, setItems] = useState<Notification[]>([])
  const [open, setOpen] = useState(false)

  const load = useCallback(() => {
    api.notifications().then(setItems).catch(() => {})
  }, [])
  useEffect(() => {
    load()
    const t = setInterval(load, 60_000)
    return () => clearInterval(t)
  }, [load])

  const unread = items.filter((n) => !n.read_at).length

  async function toggle() {
    const next = !open
    setOpen(next)
    if (next && unread > 0) {
      await api.markNotificationsRead().catch(() => {})
      load()
    }
  }

  return (
    <div className="relative">
      <button
        onClick={toggle}
        className="relative rounded-full bg-stone-100 p-2 text-stone-600"
        aria-label={`Notifications${unread ? ` (${unread} unread)` : ''}`}
      >
        <Bell className="size-4" />
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex size-4 items-center justify-center rounded-full bg-red-600 text-[10px] font-semibold text-white">
            {unread}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 mt-2 w-72 rounded-xl border border-stone-200 bg-white p-2 shadow-lg">
          {items.length === 0 && <p className="p-2 text-sm text-stone-500">Nothing new.</p>}
          <ul className="max-h-80 divide-y divide-stone-100 overflow-y-auto">
            {items.map((n) => (
              <li key={n.id} className="p-2 text-sm">
                <p>{n.message}</p>
                <p className="text-xs text-stone-500">{formatDate(n.created_at)}</p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function Tab({ to, icon, label }: { to: string; icon: React.ReactNode; label: string }) {
  return (
    <NavLink
      to={to}
      end={to === '/'}
      className={({ isActive }) =>
        `flex flex-1 flex-col items-center gap-0.5 py-2 text-[11px] ${isActive ? 'text-teal-700' : 'text-stone-500'}`
      }
    >
      {icon}
      {label}
    </NavLink>
  )
}
