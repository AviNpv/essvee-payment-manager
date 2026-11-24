import { useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import {
  Activity,
  BarChart3,
  BellRing,
  ChevronDown,
  CreditCard,
  FileSpreadsheet,
  LayoutDashboard,
  LogOut,
  Menu,
  Moon,
  Receipt,
  Settings,
  ShieldCheck,
  Sun,
  Upload,
  Users,
  X,
} from 'lucide-react'
import api, { errMsg } from '../lib/api'
import { useToast } from '../lib/Toast'
import { Modal } from './ui'

const navigation = [
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/customers', label: 'Customers', icon: Users },
  { to: '/payments', label: 'Payments', icon: CreditCard },
  { to: '/reminders', label: 'Reminders', icon: BellRing },
  { to: '/messages', label: 'Messages', icon: Receipt },
  { to: '/imports', label: 'Excel Import', icon: Upload },
  { to: '/reports', label: 'Reports', icon: BarChart3 },
  { to: '/settings', label: 'Settings', icon: Settings },
  { to: '/system', label: 'System & Audit', icon: ShieldCheck },
]

function Brand({ compact = false }) {
  return (
    <div className="flex items-center gap-3">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-700 text-sm font-bold tracking-tight text-white shadow-sm">
        EA
      </div>
      {!compact && (
        <div className="leading-tight">
          <p className="text-sm font-bold text-slate-900 dark:text-white">ESSVEE Associates</p>
          <p className="text-[11px] font-medium tracking-wide text-brand-600 dark:text-brand-400">PAYMENT MANAGER</p>
        </div>
      )}
    </div>
  )
}

export default function Layout() {
  const [dark, setDark] = useState(() => localStorage.getItem('essvee_theme') === 'dark')
  const [sidebar, setSidebar] = useState(false)
  const [user, setUser] = useState(null)
  const [userMenu, setUserMenu] = useState(false)
  const [passwordModal, setPasswordModal] = useState(false)
  const [passwords, setPasswords] = useState({ current: '', next: '' })
  const [busy, setBusy] = useState(false)
  const toast = useToast()
  const navigate = useNavigate()
  const location = useLocation()

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    localStorage.setItem('essvee_theme', dark ? 'dark' : 'light')
  }, [dark])

  useEffect(() => {
    api.get('/auth/me').then((r) => setUser(r.data)).catch(() => {})
  }, [])

  useEffect(() => setSidebar(false), [location.pathname])

  const logout = () => {
    localStorage.removeItem('essvee_token')
    navigate('/login')
  }

  const changePassword = async () => {
    setBusy(true)
    try {
      await api.post('/auth/change-password', {
        current_password: passwords.current,
        new_password: passwords.next,
      })
      toast.success('Password updated successfully')
      setPasswordModal(false)
      setPasswords({ current: '', next: '' })
    } catch (error) {
      toast.error(errMsg(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-950">
      {sidebar && (
        <div className="fixed inset-0 z-30 bg-slate-950/50 lg:hidden" onClick={() => setSidebar(false)} />
      )}
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-[248px] flex-col border-r border-slate-200 bg-white transition-transform dark:border-slate-800 dark:bg-slate-900 lg:translate-x-0 ${sidebar ? 'translate-x-0' : '-translate-x-full'}`}
      >
        <div className="flex h-16 items-center justify-between border-b border-slate-100 px-5 dark:border-slate-800">
          <Link to="/dashboard"><Brand /></Link>
          <button className="text-slate-400 lg:hidden" onClick={() => setSidebar(false)} aria-label="Close menu"><X className="h-5 w-5" /></button>
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 py-4">
          <p className="px-3 pb-2 text-[10px] font-bold uppercase tracking-widest text-slate-400">Workspace</p>
          {navigation.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition ${
                  isActive
                    ? 'bg-brand-50 text-brand-800 dark:bg-brand-500/10 dark:text-brand-300'
                    : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800'
                }`
              }
            >
              <item.icon className="h-[18px] w-[18px]" />
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-slate-100 p-4 dark:border-slate-800">
          <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-500 dark:bg-slate-800/50">
            <div className="flex items-center gap-2 font-semibold text-slate-700 dark:text-slate-200">
              <Activity className="h-3.5 w-3.5 text-brand-600" /> System ready
            </div>
            <p className="mt-1">Private deployment · Asia/Kolkata</p>
          </div>
        </div>
      </aside>

      <div className="lg:pl-[248px]">
        <header className="sticky top-0 z-20 flex h-16 items-center justify-between gap-3 border-b border-slate-200 bg-white/90 px-4 backdrop-blur dark:border-slate-800 dark:bg-slate-900/90 sm:px-6">
          <div className="flex items-center gap-3">
            <button className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800 lg:hidden" onClick={() => setSidebar(true)} aria-label="Open menu">
              <Menu className="h-5 w-5" />
            </button>
            <div>
              <p className="text-sm font-semibold text-slate-800 dark:text-white">ESSVEE Payment Manager</p>
              <p className="hidden text-[11px] text-slate-500 sm:block">Payment collections & WhatsApp reminders</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setDark((value) => !value)}
              className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
              title={dark ? 'Light mode' : 'Dark mode'}
            >
              {dark ? <Sun className="h-[18px] w-[18px]" /> : <Moon className="h-[18px] w-[18px]" />}
            </button>
            <div className="relative">
              <button
                onClick={() => setUserMenu((value) => !value)}
                className="flex items-center gap-2 rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm dark:border-slate-700"
              >
                <span className="flex h-7 w-7 items-center justify-center rounded-full bg-brand-100 text-xs font-bold text-brand-800 dark:bg-brand-500/20 dark:text-brand-200">
                  {(user?.full_name || 'A').charAt(0)}
                </span>
                <span className="hidden max-w-[140px] truncate sm:block">{user?.full_name || 'Admin'}</span>
                <ChevronDown className="h-3.5 w-3.5 text-slate-400" />
              </button>
              {userMenu && (
                <div className="absolute right-0 mt-2 w-56 rounded-xl border border-slate-200 bg-white p-1.5 shadow-lg dark:border-slate-700 dark:bg-slate-800">
                  <div className="border-b border-slate-100 px-3 py-2 dark:border-slate-700">
                    <p className="text-sm font-semibold">{user?.full_name}</p>
                    <p className="truncate text-xs text-slate-500">{user?.email}</p>
                  </div>
                  <button className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm hover:bg-slate-100 dark:hover:bg-slate-700" onClick={() => { setPasswordModal(true); setUserMenu(false) }}>
                    <ShieldCheck className="h-4 w-4" /> Change password
                  </button>
                  <button className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-500/10" onClick={logout}>
                    <LogOut className="h-4 w-4" /> Sign out
                  </button>
                </div>
              )}
            </div>
          </div>
        </header>
        <main className="mx-auto max-w-[1600px] p-4 sm:p-6 lg:p-8">
          <Outlet />
        </main>
      </div>

      <Modal open={passwordModal} onClose={() => setPasswordModal(false)} title="Change password" size="sm">
        <div className="space-y-4">
          <label className="block">
            <span className="label">Current password</span>
            <input type="password" className="input" value={passwords.current} onChange={(e) => setPasswords({ ...passwords, current: e.target.value })} />
          </label>
          <label className="block">
            <span className="label">New password (minimum 8 characters)</span>
            <input type="password" className="input" value={passwords.next} onChange={(e) => setPasswords({ ...passwords, next: e.target.value })} />
          </label>
          <div className="flex justify-end gap-2 pt-2">
            <button className="btn-secondary" onClick={() => setPasswordModal(false)}>Cancel</button>
            <button className="btn-primary" onClick={changePassword} disabled={busy || !passwords.current || passwords.next.length < 8}>Update password</button>
          </div>
        </div>
      </Modal>
    </div>
  )
}

export { Brand, FileSpreadsheet }
