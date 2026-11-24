import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, Download, FileSpreadsheet, FileUp, Upload } from 'lucide-react'
import api, { download, errMsg } from '../lib/api'
import { useToast } from '../lib/Toast'
import { date, inr } from '../lib/format'
import { Badge, Card, CardHeader, EmptyState, Field, Loading, PageHeader, Spinner } from '../components/ui'

export default function Imports() {
  const [file, setFile] = useState(null)
  const [mode, setMode] = useState('UPDATE_BALANCE')
  const [preview, setPreview] = useState(null)
  const [history, setHistory] = useState([])
  const [busy, setBusy] = useState(false)
  const [confirmDifferences, setConfirmDifferences] = useState(false)
  const toast = useToast()

  const load = () => api.get('/excel/imports').then((r) => setHistory(r.data)).catch((e) => toast.error(errMsg(e)))
  useEffect(() => { load() }, [])

  const upload = async () => {
    if (!file) return
    setBusy(true)
    try {
      const body = new FormData(); body.append('file', file); body.append('mode', mode)
      const r = await api.post('/excel/preview', body, { headers: { 'Content-Type': 'multipart/form-data' } })
      setPreview(r.data); setConfirmDifferences(false)
    } catch (e) { toast.error(errMsg(e)) } finally { setBusy(false) }
  }

  const commit = async () => {
    setBusy(true)
    try {
      const r = await api.post('/excel/import', { import_id: preview.import_id, confirm_differences: confirmDifferences })
      toast.success(r.data.message)
      setPreview(null); setFile(null); load()
    } catch (e) { toast.error(errMsg(e)) } finally { setBusy(false) }
  }

  const template = async () => {
    try { await download('/excel/template'); toast.success('Template downloaded') } catch (e) { toast.error(errMsg(e)) }
  }

  const exportCustomers = async () => {
    try { await download('/excel/export?format=xlsx'); toast.success('Customer export downloaded') } catch (e) { toast.error(errMsg(e)) }
  }

  return (
    <div className="space-y-5">
      <PageHeader title="Excel Import" subtitle="Import outstanding balances from XLSX, XLS or CSV with preview and reconciliation" actions={
        <><button className="btn-secondary" onClick={template}><Download className="h-4 w-4" /> Template</button><button className="btn-secondary" onClick={exportCustomers}><FileSpreadsheet className="h-4 w-4" /> Export customers</button></>
      } />

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Upload customer balances" subtitle="File must contain Customer Name, WhatsApp Number and Outstanding Amount" />
          <div className="space-y-4 p-5">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Excel / CSV file">
                <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-dashed border-slate-300 p-4 text-sm hover:border-brand-400 dark:border-slate-600">
                  <FileUp className="h-5 w-5 text-brand-600" />
                  <span className="min-w-0"><span className="block truncate font-medium">{file ? file.name : 'Choose a file…'}</span><span className="text-xs text-slate-500">Maximum 10 MB · up to 1,000+ rows</span></span>
                  <input type="file" className="hidden" accept=".xlsx,.xls,.csv" onChange={(e) => { setFile(e.target.files?.[0] || null); setPreview(null) }} />
                </label>
              </Field>
              <Field label="How should imported amounts be applied?" hint="Balances are always reconciled through the ledger — payment history is never overwritten.">
                <select className="input" value={mode} onChange={(e) => setMode(e.target.value)}>
                  <option value="UPDATE_BALANCE">Set current outstanding balance</option>
                  <option value="ADJUSTMENT">Apply as payment adjustment (reduces balance)</option>
                </select>
              </Field>
            </div>
            <div className="rounded-lg bg-slate-50 p-3 text-xs leading-relaxed text-slate-500 dark:bg-slate-800/50">
              Duplicate numbers are detected within the file and against existing customers. Rows with invalid names, phone numbers or amounts are excluded and reported. Every import is written to the audit log.
            </div>
            <button className="btn-primary" onClick={upload} disabled={busy || !file}>{busy ? <Spinner /> : <Upload className="h-4 w-4" />} Preview import</button>
          </div>
        </Card>

        <Card>
          <CardHeader title="Expected format" />
          <div className="p-5">
            <table className="w-full text-xs"><thead><tr className="text-left text-slate-500"><th className="pb-2">Customer Name</th><th className="pb-2">WhatsApp Number</th><th className="pb-2">Outstanding Amount</th></tr></thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {[['Rahul', '919876543210', '2500'], ['Anil', '919876543211', '5000'], ['Arun', '919876543212', '1200']].map((row) => <tr key={row[1]}>{row.map((cell) => <td key={cell} className="py-2 font-mono">{cell}</td>)}</tr>)}
              </tbody></table>
            <p className="mt-4 text-xs text-slate-500">Indian 10-digit numbers are automatically normalised to +91 format.</p>
          </div>
        </Card>
      </div>

      {preview && <Card>
        <CardHeader title="Import preview" subtitle={`${preview.row_count} rows · ${preview.valid_count} valid · ${preview.invalid_count} invalid`} action={<button className="btn-ghost text-xs" onClick={() => setPreview(null)}>Close</button>} />
        <div className="space-y-4 p-5">
          {preview.requires_difference_confirmation && <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /><span>Some rows change existing balances. You must explicitly confirm these differences before importing.</span></div>}
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={confirmDifferences} onChange={(e) => setConfirmDifferences(e.target.checked)} /> I confirm the balance differences shown below</label>
          <div className="max-h-[420px] overflow-auto rounded-lg border border-slate-200 dark:border-slate-700">
            <table className="w-full min-w-[760px] text-sm"><thead className="sticky top-0 bg-slate-50 dark:bg-slate-800"><tr><th className="th">#</th><th className="th">Name</th><th className="th">WhatsApp</th><th className="th">Import amount</th><th className="th">Current balance</th><th className="th">Difference</th><th className="th">Result</th></tr></thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">{preview.rows.map((row) => <tr key={row.row_number} className={row.status === 'INVALID' ? 'bg-rose-50/40 dark:bg-rose-500/5' : ''}>
                <td className="td">{row.row_number}</td><td className="td font-medium">{row.name || '—'}</td><td className="td font-mono text-xs">{row.normalized_phone || row.phone}</td>
                <td className="td">{row.amount != null ? inr(row.amount) : '—'}</td><td className="td">{row.current_balance != null ? inr(row.current_balance) : 'New'}</td>
                <td className="td">{row.difference != null && Number(row.difference) !== 0 ? <span className="font-semibold text-amber-600">{inr(row.difference)}</span> : '—'}</td>
                <td className="td">{row.status === 'VALID' ? <Badge tone="green">Valid</Badge> : <span className="text-xs text-rose-600" title={row.error}>{row.error}</span>}</td>
              </tr>)}</tbody></table>
          </div>
          <div className="flex justify-end gap-2"><button className="btn-secondary" onClick={() => setPreview(null)}>Cancel</button><button className="btn-primary" disabled={busy || preview.valid_count === 0 || (preview.requires_difference_confirmation && !confirmDifferences)} onClick={commit}>{busy ? <Spinner /> : <CheckCircle2 className="h-4 w-4" />} Confirm import ({preview.valid_count} rows)</button></div>
        </div>
      </Card>}

      <Card>
        <CardHeader title="Import history" subtitle="Every import is audited" />
        {history.length ? <div className="overflow-x-auto"><table className="w-full min-w-[760px]"><thead className="bg-slate-50/70 dark:bg-slate-800/50"><tr><th className="th">File</th><th className="th">Mode</th><th className="th">Rows</th><th className="th">New / Updated</th><th className="th">Status</th><th className="th">Date</th></tr></thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">{history.map((item) => <tr key={item.id}><td className="td font-medium">{item.filename}</td><td className="td">{item.mode === 'UPDATE_BALANCE' ? 'Set balance' : 'Adjustment'}</td><td className="td">{item.row_count} ({item.invalid_count} invalid)</td><td className="td">{item.imported_count} / {item.updated_count}</td><td className="td"><Badge tone={item.status === 'COMPLETED' ? 'green' : 'amber'}>{item.status}</Badge></td><td className="td">{date(item.created_at, true)}</td></tr>)}</tbody></table></div>
          : <EmptyState icon={FileSpreadsheet} title="No imports yet" description="Upload an Excel file to import customer balances." />}
      </Card>
    </div>
  )
}
