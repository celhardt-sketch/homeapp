import { Link, NavLink, Route, Routes } from 'react-router-dom'
import { Home, Settings, ShoppingCart, User } from 'lucide-react'
import { useUserName } from './useUserName'
import NamePrompt from './components/NamePrompt'
import HomePage from './pages/HomePage'
import RoomPage from './pages/RoomPage'
import AdminPage from './pages/AdminPage'
import ShoppingPage from './pages/ShoppingPage'

export default function App() {
  const { name, setName } = useUserName()

  return (
    <div className="mx-auto flex min-h-screen max-w-xl flex-col">
      <header className="sticky top-0 z-10 flex items-center justify-between border-b border-stone-200 bg-white/90 px-4 py-3 backdrop-blur">
        <Link to="/" className="flex items-center gap-2 font-semibold text-teal-800">
          <Home className="size-5" /> Home Maintenance
        </Link>
        <button
          onClick={() => setName('')}
          className="flex items-center gap-1 rounded-full bg-stone-100 px-3 py-1 text-sm text-stone-600"
          title="Change who you are"
        >
          <User className="size-4" /> {name || 'Set name'}
        </button>
      </header>

      <main className="flex-1 px-4 pb-24 pt-4">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/r/:slug" element={<RoomPage userName={name} />} />
          <Route path="/shopping" element={<ShoppingPage />} />
          <Route path="/admin" element={<AdminPage />} />
          <Route path="*" element={<p className="text-center text-stone-500">Page not found.</p>} />
        </Routes>
      </main>

      <nav className="fixed inset-x-0 bottom-0 z-10 border-t border-stone-200 bg-white pb-[env(safe-area-inset-bottom)]">
        <div className="mx-auto flex max-w-xl justify-around">
          <Tab to="/" icon={<Home className="size-5" />} label="Rooms" />
          <Tab to="/shopping" icon={<ShoppingCart className="size-5" />} label="To Buy" />
          <Tab to="/admin" icon={<Settings className="size-5" />} label="Manage" />
        </div>
      </nav>

      {!name && <NamePrompt onSubmit={setName} />}
    </div>
  )
}

function Tab({ to, icon, label }: { to: string; icon: React.ReactNode; label: string }) {
  return (
    <NavLink
      to={to}
      end={to === '/'}
      className={({ isActive }) =>
        `flex flex-1 flex-col items-center gap-0.5 py-2 text-xs ${isActive ? 'text-teal-700' : 'text-stone-500'}`
      }
    >
      {icon}
      {label}
    </NavLink>
  )
}
