import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Eye, EyeOff, LockKeyhole, Mail, ShieldCheck } from 'lucide-react'
import api, { errMsg } from '../lib/api'
import { useToast } from '../lib/Toast'
import { Brand } from '../components/Layout'

export default function Login() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [show, setShow] = useState(false)
  const [busy, setBusy] = useState(false)
  const toast = useToast()
  const navigate = useNavigate()

  const submit = async (event) => {
    event.preventDefault()
    setBusy(true)
    try {
      const { data } = await api.post('/auth/login', { email, password })
      localStorage.setItem('essvee_token', data.access_token)
      navigate('/dashboard', { replace: true })
    } catch (error) {
      toast.error(errMsg(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid min-h-screen bg-white dark:bg-slate-950 lg:grid-cols-2">
      <div className="relative hidden flex-col justify-between overflow-hidden bg-brand-900 p-12 text-white lg:flex">
        <div className="absolute -right-32 -top-32 h-[420px] w-[420px] rounded-full bg-brand-500/10 blur-3xl" />
        <div className="absolute -bottom-40 -left-24 h-[460px] w-[460px] rounded-full bg-emerald-400/10 blur-3xl" />
        <div className="relative">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-white/10 text-base font-bold ring-1 ring-white/20">EA</div>
          <h1 className="mt-16 max-w-md text-4xl font-semibold leading-tight tracking-tight">
            Payment collection, <span className="text-emerald-300">made effortless.</span>
          </h1>
          <p className="mt-4 max-w-md text-sm leading-relaxed text-brand-100/80">
            ESSVEE Associates' private platform for managing outstanding balances, WhatsApp payment
            reminders and administrator-verified collections.
          </p>
          <div className="mt-12 grid max-w-md grid-cols-2 gap-3">
            {['Google Sheets balance sync', 'Pay Now UPI links', 'Approved WhatsApp templates', 'Audited payment approval'].map((item) => (
              <div key={item} className="rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-xs font-medium text-brand-50">
                <ShieldCheck className="mb-2 h-4 w-4 text-emerald-300" />
                {item}
              </div>
            ))}
          </div>
        </div>
        <p className="relative text-xs text-brand-100/60">© {new Date().getFullYear()} ESSVEE Associates. Private use only.</p>
      </div>

      <div className="flex items-center justify-center p-6 sm:p-12">
        <div className="w-full max-w-[400px]">
          <div className="mb-10 lg:hidden"><Brand /></div>
          <h2 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">Welcome back</h2>
          <p className="mt-1 text-sm text-slate-500">Sign in to your administrator account</p>
          <form onSubmit={submit} className="mt-8 space-y-5">
            <label className="block">
              <span className="label">Email address</span>
              <div className="relative">
                <Mail className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
                <input className="input pl-9" type="email" autoComplete="username" required placeholder="admin@company.com" value={email} onChange={(e) => setEmail(e.target.value)} />
              </div>
            </label>
            <label className="block">
              <span className="label">Password</span>
              <div className="relative">
                <LockKeyhole className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
                <input className="input pl-9 pr-10" type={show ? 'text' : 'password'} autoComplete="current-password" required placeholder="••••••••" value={password} onChange={(e) => setPassword(e.target.value)} />
                <button type="button" onClick={() => setShow(!show)} className="absolute right-3 top-2.5 text-slate-400 hover:text-slate-600" aria-label="Toggle password">
                  {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </label>
            <button type="submit" className="btn-primary w-full py-2.5" disabled={busy}>
              {busy ? 'Signing in…' : 'Sign in'} {!busy && <ArrowRight className="h-4 w-4" />}
            </button>
          </form>
          <div className="mt-8 rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs leading-relaxed text-slate-500 dark:border-slate-800 dark:bg-slate-900">
            Access is restricted to authorised ESSVEE Associates personnel. Contact your system
            administrator for credentials. All financial actions are logged.
          </div>
        </div>
      </div>
    </div>
  )
}
