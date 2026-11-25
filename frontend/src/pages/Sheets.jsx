import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AlertTriangle, CheckCircle2, RefreshCw, Send, Table2 } from 'lucide-react'
import api, { errMsg } from '../lib/api'
import { useToast } from '../lib/Toast'
import { date, inr, pretty } from '../lib/format'
import { Badge, Card, CardHeader, ConfirmDialog, EmptyState, Field, Loading, PageHeader, Spinner } from '../components/ui'

const REASONS = {
  invalid_name: 'Invalid name',
  invalid_amount: 'Invalid amount',
  unmatched: 'No number in Customers tab',
  ambiguous_directory: 'Conflicting directory entry',
  invalid_phone: 'Invalid WhatsApp number',
  no_consent: 'Opted out / no consent',
  opted_out: 'Opted out / no consent',
  inactive: 'Inactive customer',
  excluded: 'Excluded from reminders',
  awaiting_confirmation: 'Payment awaiting review',
  balance_changed: 'Balance changed — preview again',
  max_reminders_reached: 'Maximum reminders reached',
  within_interval: 'Within reminder interval',
  too_early: 'First-reminder window',
  zero_balance: 'Paid in full — no reminder',
  quiet_hours: 'Quiet hours — no reminder',
  daily_limit_reached: 'Daily WhatsApp limit reached',
  eligible: 'Reminder eligible',
}

const toneFor = (status) => {
  if (['VALID', 'SENT', 'CONFIRMED'].includes(status)) return 'green'
  if (status === 'APPLIED') return 'blue'
  return 'red'
}

const changeLabel = (difference) => {
  if (difference == null || Number(difference) === 0) return '—'
  const value = Math.abs(Number(difference))
  return Number(difference) < 0 ? `−${inr(value)} payment` : `+${inr(value)} adjustment`
}

export default function Sheets() {
  const [params] = useSearchParams()
  const [status, setStatus] = useState(null)
  const [tab, setTab] = useState(params.get('tab') || 'Tuesday')
  const [spreadsheetId, setSpreadsheetId] = useState('')
  const [preview, setPreview] = useState(null)
  const [history, setHistory] = useState([])
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const toast = useToast()

  const load = () => Promise.all([api.get('/sheets/status'), api.get('/sheets/history')])
    .then(([s, h]) => {
      setStatus(s.data)
      setHistory(h.data || [])
      if (!s.data.tabs.includes(tab)) setTab(s.data.tabs[0] || 'Tuesday')
    })
    .catch((e) => toast.error(errMsg(e)))
  useEffect(() => { load() }, [])

  const buildPreview = async () => {
    setBusy(true)
    try {
      const body = { tab }
      if (spreadsheetId.trim()) body.spreadsheet_id = spreadsheetId.trim()
      const r = await api.post('/sheets/preview', body)
      setPreview(r.data); setConfirm(false)
      if (!spreadsheetId.trim()) await load()
    } catch (e) { toast.error(errMsg(e)) } finally { setBusy(false) }
  }

  const send = async () => {
    setBusy(true)
    try {
      const r = await api.post('/sheets/send', {
        import_id: preview.import_id,
        confirmation_token: preview.confirmation_token,
      })
      toast.success(r.data.message)
      setConfirm(false); setPreview(null); await load()
    } catch (e) { toast.error(errMsg(e)) } finally { setBusy(false) }
  }

  const rejectPending = async (row) => {
    const reason = window.prompt('Reason for rejecting this verified payment?')
    if (!reason) return
    setBusy(true)
    try {
      await api.post(`/sheets/pending-payments/${row.pending_payment_id}/reject`, { reason })
      toast.success('Payment rejected; the balance was not changed')
      await buildPreview()
    } catch (e) { toast.error(errMsg(e)) } finally { setBusy(false) }
  }

  if (!status) return <Loading label="Loading Google Sheets…" />

  const validRows = (preview?.rows || []).filter((row) => row.status === 'VALID')
  const paymentRows = validRows.filter((row) => row.difference != null && Number(row.difference) < 0).length
  const adjustmentRows = validRows.filter((row) => row.difference != null && Number(row.difference) > 0).length
  const counts = [
    ['Ready to apply', preview?.eligible_count ?? 0],
    ['Reminders', preview?.reminder_count ?? 0],
    ['Matched', preview?.matched_count ?? 0],
    ['Unmatched', preview?.unmatched_count ?? 0],
    ['Opted out', preview?.opted_out_count ?? 0],
    ['Skipped', preview?.invalid_count ?? 0],
  ]

  return (
    <div className="space-y-5">
      <PageHeader title="Google Sheets" subtitle="Replace a day tab with the updated list, preview it here, then explicitly confirm balances, payments and Pay Now reminders" actions={
        <button className="btn-secondary" onClick={load} disabled={busy}><RefreshCw className="h-4 w-4" /> Refresh</button>
      } />

      <div className="grid gap-4 md:grid-cols-3">
        <Card className="p-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Service account</p>
          <p className="mt-3"><Badge tone={status.configured ? 'green' : 'amber'}>{status.configured ? 'Configured' : 'Not configured'}</Badge></p>
          <p className="mt-2 truncate text-xs text-slate-500" title={status.service_account_email || ''}>{status.service_account_email || 'Store the JSON key as a Render secret'}</p>
        </Card>
        <Card className="p-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Spreadsheet</p>
          <p className="mt-3"><Badge tone={status.spreadsheet_configured ? 'green' : 'amber'}>{status.spreadsheet_configured ? 'Linked' : 'Not linked'}</Badge></p>
          <p className="mt-2 text-xs text-slate-500">Paste the share URL in Settings → Google Sheets.</p>
        </Card>
        <Card className="p-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Tabs &amp; directory</p>
          <p className="mt-3 text-sm font-semibold">{status.tabs.join(' · ')}</p>
          <p className="mt-2 text-xs text-slate-500">Day tabs: Name, Amount · Directory: {status.customers_tab} (Name, Mobile)</p>
        </Card>
      </div>

      {!status.configured && <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
        <span>Google Sheets is not configured. Add the service account JSON as <strong>GOOGLE_SERVICE_ACCOUNT_JSON</strong> in Render, share your spreadsheet with the service account email above, then link the sheet in Settings.</span>
      </div>}

      <Card>
        <CardHeader title="Build a preview" subtitle="Nothing changes until you review the rows and press Confirm" />
        <div className="grid gap-4 p-5 sm:grid-cols-3">
          <Field label="Sheet tab">
            <select className="input" value={tab} onChange={(e) => { setTab(e.target.value); setPreview(null) }}>
              {status.tabs.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </Field>
          <Field label="Spreadsheet URL (optional)" hint="Leave blank to use the linked spreadsheet in Settings.">
            <input className="input" value={spreadsheetId} onChange={(e) => setSpreadsheetId(e.target.value)} placeholder="https://docs.google.com/spreadsheets/d/…" />
          </Field>
          <div className="flex items-end"><button className="btn-primary w-full" onClick={buildPreview} disabled={busy || !status.configured}>{busy ? <Spinner /> : <Table2 className="h-4 w-4" />} Preview rows</button></div>
        </div>
      </Card>

      {preview && <Card>
        <CardHeader title={`Preview · ${preview.tab}`} subtitle={`${preview.row_count} rows · ${preview.eligible_count} ready · ${preview.reminder_count} reminders · ${preview.unmatched_count} unmatched`} action={<button className="btn-ghost text-xs" onClick={() => { setPreview(null); setConfirm(false) }}>Close</button>} />
        <div className="space-y-4 p-5">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {counts.map(([label, value]) => <div key={label} className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/60"><p className="text-[11px] uppercase text-slate-500">{label}</p><p className="mt-1 text-lg font-bold">{value}</p></div>)}
          </div>

          {preview.warnings.length > 0 && <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
            <strong>Customers tab warnings:</strong> {preview.warnings.join(' · ')}
          </div>}
          {preview.quiet_hours && <div className="flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /><span>Quiet hours are active. Balances and payments will still be applied, but Pay Now reminders will be skipped.</span>
          </div>}
          <p className="text-xs text-slate-500">
            The amount is the new outstanding balance. A lower amount is recorded as a confirmed payment, a higher amount as an audited adjustment, and a positive balance may receive one Pay Now reminder. Directory sync: {preview.directory_created} created, {preview.directory_updated} renamed.
          </p>

          <div className="max-h-[420px] overflow-auto rounded-lg border border-slate-200 dark:border-slate-700">
            <table className="w-full min-w-[900px] text-sm"><thead className="sticky top-0 bg-slate-50 dark:bg-slate-800"><tr>
              <th className="th">#</th><th className="th">Name</th><th className="th">Mobile</th>
              <th className="th">Sheet balance</th><th className="th">Current</th>
              <th className="th">Change</th><th className="th">Result</th>
            </tr></thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">{preview.rows.map((row) => <tr key={row.row_number} className={row.status === 'VALID' ? '' : 'bg-rose-50/40 dark:bg-rose-500/5'}>
                <td className="td">{row.row_number}</td>
                <td className="td font-medium">{row.name}</td>
                <td className="td font-mono text-xs">{row.phone || '—'}</td>
                <td className="td">{row.amount != null ? inr(row.amount) : '—'}</td>
                <td className="td">{row.current_balance != null ? inr(row.current_balance) : '—'}</td>
                <td className="td font-semibold text-amber-600">{changeLabel(row.difference)}</td>
                <td className="td"><div className="flex flex-wrap items-center justify-end gap-2">
                  <Badge tone={toneFor(row.status)}>{row.status === 'VALID' ? 'Ready' : REASONS[row.reason] || pretty(row.reason)}</Badge>
                  {row.status === 'VALID' && row.pending_payment_id && <Badge tone="blue">Confirm verified payment</Badge>}
                  {row.status === 'VALID' && row.message_reason && row.message_reason !== 'eligible' && <Badge tone="amber">No reminder: {REASONS[row.message_reason] || pretty(row.message_reason)}</Badge>}
                  {row.status !== 'VALID' && row.pending_payment_id && <button className="btn-ghost px-2 py-1 text-[11px]" disabled={busy} onClick={() => rejectPending(row)}>Reject payment</button>}
                </div></td>
              </tr>)}</tbody></table>
          </div>

          <div className="flex justify-end gap-2">
            <button className="btn-secondary" onClick={() => { setPreview(null); setConfirm(false) }}>Cancel</button>
            <button className="btn-primary" disabled={busy || preview.eligible_count === 0} onClick={() => setConfirm(true)}>{busy ? <Spinner /> : <Send className="h-4 w-4" />} Continue to confirmation</button>
          </div>
        </div>
      </Card>}

      <ConfirmDialog open={confirm} onClose={() => setConfirm(false)} onConfirm={send} busy={busy} danger={Boolean(preview?.quiet_hours)}
        title={`Confirm Google Sheets ${preview?.tab || ''} changes`}
        description={`You are about to apply ${preview?.eligible_count || 0} balance change(s): ${paymentRows} confirmed payment(s), ${adjustmentRows} audited adjustment(s), and ${preview?.reminder_count || 0} approved Pay Now reminder(s). This cannot be undone.${preview?.quiet_hours ? ' Quiet hours are active — reminders will be skipped.' : ''}`}
        confirmLabel={`Confirm changes (${preview?.eligible_count || 0})`} />

      <Card>
        <CardHeader title="Sheet history" subtitle="Every sheet preview and confirmation is recorded in the audit log" />
        {history.length ? <div className="overflow-x-auto"><table className="w-full min-w-[720px]"><thead className="bg-slate-50/70 dark:bg-slate-800/50"><tr><th className="th">Tab</th><th className="th">Rows</th><th className="th">Applied</th><th className="th">Messages</th><th className="th">Status</th><th className="th">Date</th></tr></thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">{history.map((item) => <tr key={item.id}>
            <td className="td font-medium">{item.filename}</td>
            <td className="td">{item.row_count}</td>
            <td className="td">{item.imported_count}</td>
            <td className="td">{item.updated_count}</td>
            <td className="td"><Badge tone={item.status === 'COMPLETED' ? 'green' : 'amber'}>{item.status}</Badge></td>
            <td className="td">{date(item.created_at, true)}</td>
          </tr>)}</tbody></table></div>
          : <EmptyState icon={CheckCircle2} title="No sheet activity yet" description="Paste balances into a day tab, then preview and confirm them here." />}
      </Card>
    </div>
  )
}
