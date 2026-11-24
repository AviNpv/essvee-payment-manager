import { useEffect, useState } from 'react'
import { Activity, Database, ShieldCheck, Webhook } from 'lucide-react'
import api, { errMsg } from '../lib/api'
import { useToast } from '../lib/Toast'
import { date, pretty } from '../lib/format'
import { Badge, Card, CardHeader, EmptyState, Loading, PageHeader } from '../components/ui'

export default function System() {
  const [health, setHealth] = useState(null)
  const [audit, setAudit] = useState([])
  const [webhooks, setWebhooks] = useState([])
  const [runs, setRuns] = useState([])
  const [loading, setLoading] = useState(true)
  const toast = useToast()

  useEffect(() => {
    Promise.all([
      api.get('/health'), api.get('/audit-logs?limit=80'), api.get('/webhook-events?limit=40'), api.get('/scheduler/runs?limit=20'),
    ]).then(([h, a, w, r]) => { setHealth(h.data); setAudit(a.data); setWebhooks(w.data); setRuns(r.data) })
      .catch((e) => toast.error(errMsg(e))).finally(() => setLoading(false))
  }, [])

  if (loading) return <Loading label="Loading system information…" />

  return (
    <div className="space-y-5">
      <PageHeader title="System & audit" subtitle="Health, webhook reconciliation and financial audit trail" />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[
          ['Application', health?.status, Activity], ['Database', health?.database, Database],
          ['Environment', health?.environment, ShieldCheck], ['Razorpay mode', health?.razorpay_mode, Webhook],
        ].map(([label, value, Icon]) => <Card key={label} className="p-4"><div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500"><Icon className="h-4 w-4 text-brand-600" /> {label}</div><p className="mt-3 text-lg font-bold capitalize">{value || '—'}</p></Card>)}
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Webhook events" subtitle="Signature verification and processing status" />
          {webhooks.length ? <div className="divide-y divide-slate-100 dark:divide-slate-800">{webhooks.map((event) => <div key={event.id} className="flex items-center justify-between gap-3 px-5 py-2.5"><div className="min-w-0"><p className="truncate text-sm font-medium">{pretty(event.event_type)}</p><p className="text-xs text-slate-500">{event.provider} · {date(event.received_at, true)}</p></div><div className="flex gap-1"><Badge tone={event.signature_valid ? 'green' : 'red'}>{event.signature_valid ? 'Verified' : 'Invalid'}</Badge><Badge tone={event.processed ? 'blue' : 'amber'}>{pretty(event.processed ? 'processed' : 'pending')}</Badge></div></div>)}</div> : <EmptyState icon={Webhook} title="No webhooks received" />}
        </Card>
        <Card>
          <CardHeader title="Job executions" />
          {runs.length ? <div className="divide-y divide-slate-100 dark:divide-slate-800">{runs.map((run) => <div key={run.id} className="flex items-center justify-between px-5 py-2.5"><p className="text-sm font-medium">{pretty(run.job_name)}</p><div className="text-right"><Badge tone={run.status === 'COMPLETED' ? 'green' : 'amber'}>{pretty(run.status)}</Badge><p className="mt-0.5 text-xs text-slate-500">{run.processed} processed · {run.failed} failed · {date(run.started_at, true)}</p></div></div>)}</div> : <EmptyState title="No jobs executed" />}
        </Card>
      </div>

      <Card>
        <CardHeader title="Audit log" subtitle="All financial and administrative actions" />
        {audit.length ? <div className="overflow-x-auto"><table className="w-full min-w-[760px]"><thead className="bg-slate-50/70 dark:bg-slate-800/50"><tr><th className="th">Timestamp</th><th className="th">Action</th><th className="th">Entity</th><th className="th">Actor</th><th className="th">Details</th></tr></thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">{audit.map((entry) => <tr key={entry.id}><td className="td whitespace-nowrap text-xs">{date(entry.created_at, true)}</td><td className="td font-medium">{pretty(entry.action)}</td><td className="td text-xs">{entry.entity_type} {entry.entity_id?.slice(0, 8)}</td><td className="td text-xs">{entry.actor_email || 'System'}</td><td className="td max-w-[320px] truncate text-xs text-slate-500" title={JSON.stringify(entry.after)}>{JSON.stringify(entry.after || {})}</td></tr>)}</tbody></table></div> : <EmptyState icon={ShieldCheck} title="No audit entries yet" />}
      </Card>
    </div>
  )
}
