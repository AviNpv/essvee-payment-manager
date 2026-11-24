import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import {
  AlertTriangle, ArrowRight, BellRing, CheckCircle2, CreditCard, Download,
  Link2, Plus, Receipt, TrendingUp, Upload, Users, Wallet,
} from 'lucide-react'
import api, { download, errMsg } from '../lib/api'
import { useToast } from '../lib/Toast'
import { date, inr, num, pretty, statusTone } from '../lib/format'
import { Badge, Card, CardHeader, EmptyState, Loading, PageHeader, StatCard } from '../components/ui'

const COLORS = ['#2d6149', '#599478', '#82b49a', '#cbd5e1']

export default function Dashboard() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const toast = useToast()
  const navigate = useNavigate()

  const load = () => api.get('/reports/dashboard').then((r) => setData(r.data)).catch((e) => setError(errMsg(e)))
  useEffect(() => { load() }, [])

  if (error && !data) return <Card><EmptyState icon={AlertTriangle} title="Unable to load dashboard" description={error} action={<button className="btn-primary" onClick={load}>Retry</button>} /></Card>
  if (!data) return <Loading label="Loading dashboard…" />

  const { stats, charts } = data
  const quickActions = [
    { label: 'Add customer', icon: Plus, to: '/customers?new=1' },
    { label: 'Upload Excel', icon: Upload, to: '/imports' },
    { label: 'Send reminder', icon: BellRing, to: '/reminders' },
    { label: 'Generate link', icon: Link2, to: '/customers' },
    { label: 'Verify payment', icon: CheckCircle2, to: '/payments?tab=AWAITING_CONFIRMATION' },
    { label: 'Export report', icon: Download, action: async () => {
      try { await download('/reports/export', { report: 'collections', format: 'xlsx' }, 'post'); toast.success('Report downloaded') }
      catch (e) { toast.error(errMsg(e)) }
    } },
  ]

  return (
    <div className="space-y-6">
      <PageHeader
        title="Dashboard"
        subtitle="Financial overview and collection activity"
        actions={<span className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300">ESSVEE Associates</span>}
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Total outstanding" value={inr(stats.total_outstanding)} icon={Wallet} hint={`${num(stats.customers_with_outstanding)} customers pending`} to="/customers?outstanding=pending" />
        <StatCard label="Confirmed collections" value={inr(stats.total_collected)} icon={TrendingUp} tone="green" hint="All-time confirmed payments" to="/payments?tab=CONFIRMED" />
        <StatCard label="Pending verification" value={inr(stats.pending_amount)} icon={Receipt} tone="amber" hint={`${num(stats.pending_count)} payment(s) awaiting review`} to="/payments?tab=AWAITING_CONFIRMATION" />
        <StatCard label="Total customers" value={num(stats.total_customers)} icon={Users} tone="blue" hint="Registered customers" to="/customers" />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Collections trend" subtitle="Confirmed payments over the last 14 days" />
          <div className="h-64 px-3 py-4">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={charts.daily_collections} margin={{ top: 5, right: 12, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="collectionFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#2d6149" stopOpacity={0.28} />
                    <stop offset="100%" stopColor="#2d6149" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
                <XAxis dataKey="label" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
                <YAxis tick={{ fontSize: 11 }} tickLine={false} axisLine={false} tickFormatter={(v) => `₹${v}`} />
                <Tooltip formatter={(v) => [inr(v), 'Collected']} contentStyle={{ borderRadius: 10, fontSize: 12 }} />
                <Area type="monotone" dataKey="amount" stroke="#2d6149" strokeWidth={2} fill="url(#collectionFill)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <div className="grid gap-4 border-t border-slate-100 px-5 py-4 dark:border-slate-800 sm:grid-cols-2">
            <div>
              <p className="mb-2 text-xs font-semibold text-slate-500">Weekly collections</p>
              <div className="h-32">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={charts.weekly_collections}>
                    <XAxis dataKey="label" tick={{ fontSize: 10 }} axisLine={false} tickLine={false} />
                    <YAxis tick={{ fontSize: 10 }} axisLine={false} tickLine={false} width={42} />
                    <Tooltip formatter={(v) => [inr(v), 'Collected']} contentStyle={{ borderRadius: 10, fontSize: 12 }} />
                    <Bar dataKey="amount" fill="#82b49a" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
            <div>
              <p className="mb-2 text-xs font-semibold text-slate-500">Monthly collections</p>
              <div className="h-32">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={charts.monthly_collections}>
                    <XAxis dataKey="label" tick={{ fontSize: 10 }} axisLine={false} tickLine={false} />
                    <YAxis tick={{ fontSize: 10 }} axisLine={false} tickLine={false} width={42} />
                    <Tooltip formatter={(v) => [inr(v), 'Collected']} contentStyle={{ borderRadius: 10, fontSize: 12 }} />
                    <Bar dataKey="amount" fill="#599478" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader title="Payment methods" subtitle="Confirmed payments" />
            <div className="h-56 p-3">
              {charts.payment_methods.length ? (
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={charts.payment_methods} dataKey="amount" nameKey="method" innerRadius={45} outerRadius={72} paddingAngle={3}>
                      {charts.payment_methods.map((entry, index) => <Cell key={entry.method} fill={COLORS[index % COLORS.length]} />)}
                    </Pie>
                    <Tooltip formatter={(v) => inr(v)} contentStyle={{ borderRadius: 10, fontSize: 12 }} />
                    <Legend iconType="circle" wrapperStyle={{ fontSize: 11 }} />
                  </PieChart>
                </ResponsiveContainer>
              ) : <EmptyState title="No confirmed payments" description="Payment methods will appear here." />}
            </div>
          </Card>
          <Card>
            <CardHeader title="Quick actions" />
            <div className="grid grid-cols-2 gap-2 p-3">
              {quickActions.map((action) => (
                <button key={action.label} onClick={() => action.to ? navigate(action.to) : action.action()} className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2.5 text-left text-xs font-semibold text-slate-700 transition hover:border-brand-300 hover:bg-brand-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800">
                  <action.icon className="h-4 w-4 text-brand-600" /> {action.label}
                </button>
              ))}
            </div>
          </Card>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Awaiting verification" subtitle="Payments detected automatically — review before approval" action={<button className="text-xs font-semibold text-brand-600 hover:underline" onClick={() => navigate('/payments?tab=AWAITING_CONFIRMATION')}>View all <ArrowRight className="inline h-3 w-3" /></button>} />
          {data.awaiting_verification.length ? (
            <div className="divide-y divide-slate-100 dark:divide-slate-800">
              {data.awaiting_verification.map((payment) => (
                <div key={payment.id} className="flex items-center justify-between gap-3 px-5 py-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold">{payment.customer_name}</p>
                    <p className="text-xs text-slate-500">{pretty(payment.method)} · {date(payment.payment_date, true)}</p>
                  </div>
                  <div className="text-right">
                    <p className="text-sm font-bold text-slate-900 dark:text-white">{inr(payment.amount)}</p>
                    <Badge tone="amber">Awaiting</Badge>
                  </div>
                </div>
              ))}
            </div>
          ) : <EmptyState icon={CheckCircle2} title="Nothing to verify" description="New Razorpay payments will appear here." />}
        </Card>
        <Card>
          <CardHeader title="Highest outstanding balances" action={<button className="text-xs font-semibold text-brand-600 hover:underline" onClick={() => navigate('/customers?outstanding=pending')}>View all <ArrowRight className="inline h-3 w-3" /></button>} />
          {data.top_customers.length ? (
            <div className="divide-y divide-slate-100 dark:divide-slate-800">
              {data.top_customers.map((customer, index) => (
                <div key={customer.id} className="flex items-center gap-3 px-5 py-3">
                  <span className="flex h-7 w-7 items-center justify-center rounded-full bg-slate-100 text-xs font-bold text-slate-500 dark:bg-slate-800">{index + 1}</span>
                  <p className="flex-1 truncate text-sm font-medium">{customer.name}</p>
                  <p className="text-sm font-bold text-rose-600">{inr(customer.outstanding_balance)}</p>
                </div>
              ))}
            </div>
          ) : <EmptyState icon={Wallet} title="No outstanding balances" description="Import customers to get started." />}
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader title="Recent payments" />
          {data.recent_payments.length ? (
            <div className="divide-y divide-slate-100 dark:divide-slate-800">
              {data.recent_payments.map((p) => (
                <div key={p.id} className="flex items-center justify-between px-5 py-2.5">
                  <div><p className="text-sm font-medium">{p.customer_name}</p><p className="text-xs text-slate-500">{date(p.payment_date)}</p></div>
                  <div className="text-right"><p className="text-sm font-semibold">{inr(p.amount)}</p><span className={`badge ${statusTone(p.status)}`}>{pretty(p.status)}</span></div>
                </div>
              ))}
            </div>
          ) : <EmptyState title="No payments yet" />}
        </Card>
        <Card>
          <CardHeader title="Recent WhatsApp messages" subtitle={`${num(stats.reminders_sent)} reminders sent · ${num(stats.successful_deliveries)} delivered · ${num(stats.failed_messages)} failed`} />
          {data.recent_messages.length ? (
            <div className="divide-y divide-slate-100 dark:divide-slate-800">
              {data.recent_messages.map((m) => (
                <div key={m.id} className="flex items-center justify-between gap-2 px-5 py-2.5">
                  <div className="min-w-0"><p className="truncate text-sm font-medium">{m.customer_name || 'Unknown'}</p><p className="text-xs text-slate-500">{pretty(m.message_type)} · {date(m.created_at, true)}</p></div>
                  <span className={`badge ${statusTone(m.status)}`}>{pretty(m.status)}</span>
                </div>
              ))}
            </div>
          ) : <EmptyState icon={BellRing} title="No messages yet" />}
        </Card>
        <Card>
          <CardHeader title="Failed reminders" subtitle="Retry from the Messages page" />
          {data.failed_reminders.length ? (
            <div className="divide-y divide-slate-100 dark:divide-slate-800">
              {data.failed_reminders.map((m) => (
                <div key={m.id} className="px-5 py-2.5">
                  <p className="text-sm font-medium">{m.customer_name || 'Unknown'}</p>
                  <p className="line-clamp-2 text-xs text-rose-600">{m.error || 'Delivery failed'}</p>
                </div>
              ))}
            </div>
          ) : <EmptyState icon={CheckCircle2} title="No failed messages" />}
        </Card>
      </div>
    </div>
  )
}
