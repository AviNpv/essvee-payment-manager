import { useEffect, useState } from 'react'
import { Activity, BellRing, CheckCircle2, Clock3, Play, Send, Settings2, Users } from 'lucide-react'
import api, { errMsg } from '../lib/api'
import { useToast } from '../lib/Toast'
import { date, inr, pretty } from '../lib/format'
import { Badge, Card, CardHeader, ConfirmDialog, EmptyState, Field, Loading, PageHeader, Spinner } from '../components/ui'

export default function Reminders() {
  const [settings, setSettings] = useState(null)
  const [preview, setPreview] = useState(null)
  const [runs, setRuns] = useState([])
  const [customers, setCustomers] = useState([])
  const [selected, setSelected] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [jobResult, setJobResult] = useState(null)
  const toast = useToast()

  const load = () => {
    setLoading(true)
    Promise.all([
      api.get('/settings'),
      api.get('/scheduler/runs?limit=12'),
      api.get('/customers?page_size=200&outstanding=pending'),
    ]).then(([s, r, c]) => {
      setSettings(s.data); setRuns(r.data); setCustomers(c.data.items)
    }).catch((e) => toast.error(errMsg(e))).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  const buildPreview = async () => {
    setBusy(true)
    try {
      const r = await api.post('/whatsapp/reminders/preview', { respect_quiet_hours: true })
      setPreview(r.data)
    } catch (e) { toast.error(errMsg(e)) } finally { setBusy(false) }
  }

  const sendBulk = async () => {
    setBusy(true)
    try {
      const r = await api.post('/whatsapp/reminders/bulk', { confirmation_token: preview.confirmation_token })
      toast.success(`Reminders dispatched — ${r.data.sent} sent, ${r.data.failed} failed, ${r.data.skipped} skipped`)
      setConfirm(false); setPreview(null); load()
    } catch (e) { toast.error(errMsg(e)) } finally { setBusy(false) }
  }

  const sendOne = async () => {
    if (!selected) return
    setBusy(true)
    try {
      const r = await api.post('/whatsapp/reminders/send', { customer_id: selected })
      toast.success(r.data.status === 'FAILED' ? `Message failed: ${r.data.error}` : 'Reminder sent successfully')
      setSelected(''); load()
    } catch (e) { toast.error(errMsg(e)) } finally { setBusy(false) }
  }

  const runJob = async (job) => {
    setBusy(true)
    try {
      const r = await api.post(`/scheduler/run?job=${job}`)
      setJobResult({ job, result: r.data }); toast.success(`${pretty(job)} job completed`); load()
    } catch (e) { toast.error(errMsg(e)) } finally { setBusy(false) }
  }

  if (loading || !settings) return <Loading label="Loading reminders…" />

  const config = settings.reminder
  return (
    <div className="space-y-5">
      <PageHeader title="Reminders" subtitle="Automated and manual WhatsApp payment reminders" actions={
        <>
          <button className="btn-secondary" onClick={() => runJob('reconcile')} disabled={busy}><Activity className="h-4 w-4" /> Reconcile links</button>
          <button className="btn-secondary" onClick={() => runJob('retry')} disabled={busy}><Play className="h-4 w-4" /> Retry failures</button>
          <button className="btn-primary" onClick={buildPreview} disabled={busy}>{busy ? <Spinner /> : <BellRing className="h-4 w-4" />} Bulk reminder</button>
        </>
      } />

      <div className="grid gap-4 md:grid-cols-3">
        <Card className="p-4">
          <div className="flex items-center justify-between"><p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Automation</p><Badge tone={config.automation_enabled ? 'green' : 'amber'}>{config.automation_enabled ? 'Enabled' : 'Paused'}</Badge></div>
          <p className="mt-3 text-2xl font-bold">{config.automation_enabled ? 'Running' : 'Paused'}</p>
          <p className="mt-1 text-xs text-slate-500">Every {config.frequency_days} day(s) · max {config.max_reminders} reminders</p>
        </Card>
        <Card className="p-4">
          <div className="flex items-center gap-2"><Clock3 className="h-4 w-4 text-brand-600" /><p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Quiet hours</p></div>
          <p className="mt-3 text-2xl font-bold">{config.quiet_start} – {config.quiet_end}</p>
          <p className="mt-1 text-xs text-slate-500">Asia/Kolkata · no automated sends during this window</p>
        </Card>
        <Card className="p-4">
          <div className="flex items-center gap-2"><Users className="h-4 w-4 text-brand-600" /><p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Daily limit</p></div>
          <p className="mt-3 text-2xl font-bold">{config.daily_limit}</p>
          <p className="mt-1 text-xs text-slate-500">Messages per day across all customers</p>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Send an individual reminder" subtitle="Generates or reuses a customer's Razorpay link" />
          <div className="space-y-4 p-5">
            <Field label="Customer with outstanding balance"><select className="input" value={selected} onChange={(e) => setSelected(e.target.value)}><option value="">Select a customer…</option>{customers.map((c) => <option key={c.id} value={c.id}>{c.name} · {inr(c.outstanding_balance)}</option>)}</select></Field>
            <div className="rounded-lg bg-slate-50 p-3 text-xs leading-relaxed text-slate-500 dark:bg-slate-800/50">
              Reminders use the approved <strong>{config.reminder_template}</strong> template with a Pay Now button. Customers must have opted in and must not have a payment awaiting review.
            </div>
            <button className="btn-primary" onClick={sendOne} disabled={busy || !selected}>{busy ? <Spinner /> : <Send className="h-4 w-4" />} Send reminder</button>
          </div>
        </Card>
        <Card>
          <CardHeader title="Automation configuration" subtitle="Change these in Settings" action={<button className="text-xs font-semibold text-brand-600 hover:underline" onClick={() => window.location.assign('/settings')}>Open settings →</button>} />
          <div className="grid grid-cols-2 gap-3 p-5 text-sm">
            {[
              ['First reminder after', `${config.first_reminder_after_days} day(s)`],
              ['Repeat frequency', `Every ${config.frequency_days} day(s)`],
              ['Maximum reminders', config.max_reminders],
              ['Partial payments', settings.payment.allow_partial_payments ? 'Enabled' : 'Disabled'],
              ['Link expiry', `${settings.payment.link_expiry_days} days`],
              ['Retry failed messages', config.retry_failed_messages ? 'Enabled' : 'Disabled'],
            ].map(([label, value]) => <div key={label} className="rounded-lg border border-slate-200 px-3 py-2.5 dark:border-slate-700"><p className="text-xs text-slate-500">{label}</p><p className="mt-0.5 font-semibold">{value}</p></div>)}
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader title="Recent job executions" subtitle="Database-backed automation history" />
        {runs.length ? (
          <div className="overflow-x-auto"><table className="w-full min-w-[600px]"><thead className="bg-slate-50/70 dark:bg-slate-800/50"><tr><th className="th">Job</th><th className="th">Status</th><th className="th">Processed</th><th className="th">Failed</th><th className="th">Started</th></tr></thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">{runs.map((run) => <tr key={run.id}><td className="td font-medium">{pretty(run.job_name)}</td><td className="td"><Badge tone={run.status === 'COMPLETED' ? 'green' : run.status === 'SKIPPED' ? 'amber' : 'blue'}>{pretty(run.status)}</Badge></td><td className="td">{run.processed}</td><td className="td">{run.failed}</td><td className="td">{date(run.started_at, true)}</td></tr>)}</tbody></table></div>
        ) : <EmptyState icon={Settings2} title="No job executions yet" />}
      </Card>

      {jobResult && <div className="rounded-lg border border-brand-200 bg-brand-50 p-3 text-xs text-brand-800 dark:border-brand-500/30 dark:bg-brand-500/10 dark:text-brand-200">Last result · {pretty(jobResult.job)}: {JSON.stringify(jobResult.result)}</div>}

      <ModalPreview preview={preview} onClose={() => setPreview(null)} onSend={() => setConfirm(true)} busy={busy} />
      <ConfirmDialog open={confirm} onClose={() => setConfirm(false)} onConfirm={sendBulk} busy={busy} danger={preview?.quiet_hours}
        title="Confirm bulk WhatsApp reminders"
        description={`You are about to send ${preview?.eligible_count} reminder message(s) to eligible customers. Estimated volume: ${preview?.estimated_messages}. Total outstanding: ${inr(preview?.total_outstanding || 0)}.${preview?.quiet_hours ? ' Quiet hours are active — sending will be blocked.' : ''}`}
        confirmLabel={preview?.quiet_hours ? 'Understood' : `Send ${preview?.eligible_count || 0} reminders`}
      />
    </div>
  )
}

function ModalPreview({ preview, onClose, onSend, busy }) {
  if (!preview) return null
  const reasons = Object.entries(preview.reason_breakdown || {}).filter(([key]) => key !== 'eligible')
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-950/60 p-4 backdrop-blur-sm sm:p-8">
      <div className="fade-in w-full max-w-3xl rounded-2xl bg-white shadow-2xl dark:bg-slate-900">
        <div className="flex items-start justify-between border-b border-slate-100 px-5 py-4 dark:border-slate-800">
          <div><h2 className="text-base font-semibold">Bulk reminder preview</h2><p className="text-sm text-slate-500">Review audience before sending</p></div>
          <button className="text-slate-400" onClick={onClose}>✕</button>
        </div>
        <div className="space-y-5 p-5">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[['Eligible customers', preview.eligible_count], ['Estimated messages', preview.estimated_messages], ['Excluded', preview.excluded_count], ['Total outstanding', inr(preview.total_outstanding)]].map(([label, value]) => <div key={label} className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/60"><p className="text-[11px] uppercase text-slate-500">{label}</p><p className="mt-1 text-lg font-bold">{value}</p></div>)}
          </div>
          {reasons.length > 0 && <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">Excluded reasons: {reasons.map(([k, v]) => `${pretty(k)} (${v})`).join(', ')}</div>}
          <div className="max-h-72 overflow-y-auto rounded-lg border border-slate-200 dark:border-slate-700">
            {preview.customers.length ? preview.customers.map((c) => <div key={c.id} className="flex items-center justify-between border-b border-slate-100 px-3 py-2 text-sm last:border-0 dark:border-slate-800"><span className="font-medium">{c.name}</span><span className="text-slate-500">{c.whatsapp_number} · {inr(c.outstanding_balance)}</span></div>) : <EmptyState icon={CheckCircle2} title="No eligible customers" description="Nobody requires a reminder right now." />}
          </div>
          <div className="flex justify-end gap-2"><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" onClick={onSend} disabled={busy || preview.eligible_count === 0}><Send className="h-4 w-4" /> Continue to confirmation</button></div>
        </div>
      </div>
    </div>
  )
}
