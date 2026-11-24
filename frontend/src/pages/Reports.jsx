import { useEffect, useState } from 'react'
import { BarChart3, Download, FileSpreadsheet } from 'lucide-react'
import api, { download, errMsg } from '../lib/api'
import { useToast } from '../lib/Toast'
import { inr, pretty } from '../lib/format'
import { Card, CardHeader, EmptyState, Field, Loading, PageHeader, Spinner } from '../components/ui'

const reports = [
  { key: 'collections', label: 'Collections / payment history' },
  { key: 'outstanding', label: 'Customer-wise outstanding balances' },
  { key: 'whatsapp', label: 'WhatsApp reminder report' },
  { key: 'failed-payments', label: 'Failed payments' },
  { key: 'pending-verification', label: 'Pending verification' },
]

export default function Reports() {
  const [report, setReport] = useState('collections')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const toast = useToast()

  const load = () => {
    setLoading(true)
    const query = new URLSearchParams()
    if (from) query.set('from_date', new Date(from).toISOString())
    if (to) query.set('to_date', new Date(to).toISOString())
    api.get(`/reports/${report}?${query}`).then((r) => setRows(r.data.rows)).catch((e) => toast.error(errMsg(e))).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [report])

  const exportFile = async (format) => {
    setBusy(true)
    try {
      await download('/reports/export', {
        report, format,
        from_date: from ? new Date(from).toISOString() : null,
        to_date: to ? new Date(to).toISOString() : null,
      }, 'post')
      toast.success(`${format.toUpperCase()} report downloaded`)
    } catch (e) { toast.error(errMsg(e)) } finally { setBusy(false) }
  }

  const columns = rows.length ? Object.keys(rows[0]) : []
  const moneyColumns = new Set(['Amount', 'Outstanding Balance', 'Total Paid', 'Opening Balance'])

  return (
    <div className="space-y-5">
      <PageHeader title="Reports" subtitle="Collection, balance and WhatsApp activity reports" actions={
        <>
          <button className="btn-secondary" onClick={() => exportFile('csv')} disabled={busy}>CSV</button>
          <button className="btn-primary" onClick={() => exportFile('xlsx')} disabled={busy}>{busy ? <Spinner /> : <Download className="h-4 w-4" />} Export Excel</button>
        </>
      } />
      <Card>
        <div className="flex flex-wrap items-end gap-3 p-4">
          <div className="min-w-[220px] flex-1"><Field label="Report type"><select className="input" value={report} onChange={(e) => setReport(e.target.value)}>{reports.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}</select></Field></div>
          <Field label="From date"><input className="input" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
          <Field label="To date"><input className="input" type="date" value={to} onChange={(e) => setTo(e.target.value)} /></Field>
          <button className="btn-secondary" onClick={load}>Apply filter</button>
        </div>
        {loading ? <Loading /> : rows.length === 0 ? (
          <EmptyState icon={FileSpreadsheet} title="No records for this report" description="Try a different date range or report type." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px]"><thead className="bg-slate-50/70 dark:bg-slate-800/50"><tr>{columns.map((column) => <th key={column} className="th whitespace-nowrap">{pretty(column)}</th>)}</tr></thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">{rows.slice(0, 500).map((row, index) => <tr key={index} className="hover:bg-slate-50/60 dark:hover:bg-slate-800/30">{columns.map((column) => <td key={column} className="td whitespace-nowrap">{moneyColumns.has(column) ? inr(row[column]) : String(row[column] ?? '—')}</td>)}</tr>)}</tbody></table>
            {rows.length > 500 && <p className="px-5 py-3 text-xs text-slate-500">Showing first 500 rows. Export the full report to Excel for all {rows.length} records.</p>}
          </div>
        )}
      </Card>
      <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white p-3 text-xs text-slate-500 dark:border-slate-800 dark:bg-slate-900"><BarChart3 className="h-4 w-4 text-brand-600" /> Reports are based on confirmed transactions only. Date filters never use invoice due dates.</div>
    </div>
  )
}
