import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CheckCircle2, CreditCard, Link2, Plus, Receipt, RotateCcw, Search, XCircle } from 'lucide-react'
import api, { errMsg } from '../lib/api'
import { useToast } from '../lib/Toast'
import { date, inr, pretty, statusTone } from '../lib/format'
import { Badge, Card, ConfirmDialog, EmptyState, Field, Loading, Modal, PageHeader, Pagination, SearchInput } from '../components/ui'

const tabs = [
  { key: '', label: 'All payments' },
  { key: 'AWAITING_CONFIRMATION', label: 'Awaiting confirmation' },
  { key: 'CONFIRMED', label: 'Confirmed' },
  { key: 'REJECTED', label: 'Rejected' },
  { key: 'FAILED', label: 'Failed' },
  { key: 'REVERSED', label: 'Reversed' },
]

export default function Payments() {
  const [params, setParams] = useSearchParams()
  const [tab, setTab] = useState(params.get('tab') || '')
  const [search, setSearch] = useState('')
  const [method, setMethod] = useState('')
  const [page, setPage] = useState(1)
  const [data, setData] = useState({ items: [], total: 0, pages: 1, page: 1 })
  const [loading, setLoading] = useState(true)
  const [customers, setCustomers] = useState([])
  const [manual, setManual] = useState(null)
  const [dialog, setDialog] = useState(null)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const toast = useToast()

  const load = () => {
    setLoading(true)
    const query = new URLSearchParams({ page, page_size: 25 })
    if (tab) query.set('status', tab)
    if (search) query.set('search', search)
    if (method) query.set('method', method)
    api.get(`/payments?${query}`).then((r) => setData(r.data)).catch((e) => toast.error(errMsg(e))).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [tab, search, method, page])
  useEffect(() => { api.get('/customers?page_size=200').then((r) => setCustomers(r.data.items)).catch(() => {}) }, [])
  useEffect(() => { setTab(params.get('tab') || '') }, [params])

  const act = async () => {
    if (!dialog) return
    setBusy(true)
    try {
      const id = dialog.payment.id
      if (dialog.type === 'confirm') {
        const r = await api.post(`/payments/${id}/confirm`)
        toast.success(`Payment confirmed. Balance now ${inr(r.data.balance_after)}. ${r.data.message_status}`)
      } else if (dialog.type === 'reject') {
        await api.post(`/payments/${id}/reject`, { reason })
        toast.success('Payment rejected')
      } else {
        await api.post(`/payments/${id}/reverse`, { reason })
        toast.success('Payment reversed with ledger entry')
      }
      setDialog(null); setReason(''); load()
    } catch (e) { toast.error(errMsg(e)) } finally { setBusy(false) }
  }

  const saveManual = async () => {
    setBusy(true)
    try {
      await api.post('/payments/manual', manual)
      toast.success('Manual payment recorded — awaiting confirmation')
      setManual(null); load()
    } catch (e) { toast.error(errMsg(e)) } finally { setBusy(false) }
  }

  return (
    <div className="space-y-5">
      <PageHeader title="Payments" subtitle="Verify gateway payments and record offline collections" actions={
        <><button className="btn-secondary" onClick={() => api.get('/payment-links').then((r) => {
          if (!r.data.length) return toast.info('No payment links generated yet')
          window.open(r.data[0].url, '_blank', 'noopener')
        })}><Link2 className="h-4 w-4" /> Latest link</button>
        <button className="btn-primary" onClick={() => setManual({ customer_id: '', amount: '', method: 'CASH', reference: '', notes: '' })}><Plus className="h-4 w-4" /> Record payment</button></>
      } />

      <Card>
        <div className="flex flex-wrap gap-1 border-b border-slate-100 px-3 pt-2 dark:border-slate-800">
          {tabs.map((item) => (
            <button key={item.key} onClick={() => { setTab(item.key); setPage(1); setParams(item.key ? { tab: item.key } : {}) }}
              className={`rounded-t-lg px-3 py-2 text-xs font-semibold transition ${tab === item.key ? 'border-b-2 border-brand-600 text-brand-700 dark:text-brand-300' : 'text-slate-500 hover:text-slate-800 dark:hover:text-slate-200'}`}>
              {item.label}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-3 p-4">
          <div className="flex-1"><SearchInput value={search} onChange={(v) => { setSearch(v); setPage(1) }} placeholder="Customer, reference or gateway ID…" /></div>
          <select className="input w-auto min-w-[150px]" value={method} onChange={(e) => { setMethod(e.target.value); setPage(1) }}>
            <option value="">All methods</option><option>RAZORPAY</option><option>CASH</option><option>BANK_TRANSFER</option><option>UPI</option><option>OTHER</option>
          </select>
        </div>
        {loading ? <Loading /> : data.items.length === 0 ? (
          <EmptyState icon={Receipt} title="No payments found" description="UPI payments confirmed from customer UTRs, Razorpay webhook payments, and manually recorded collections appear here." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px]">
              <thead className="bg-slate-50/70 dark:bg-slate-800/50"><tr>
                <th className="th">Customer</th><th className="th">Amount</th><th className="th">Method</th>
                <th className="th">Gateway transaction ID</th><th className="th">Date</th><th className="th">Status</th><th className="th text-right">Actions</th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {data.items.map((payment) => (
                  <tr key={payment.id} className="hover:bg-slate-50/60 dark:hover:bg-slate-800/30">
                    <td className="td"><span className="font-semibold">{payment.customer_name}</span><span className="block text-xs text-slate-400">{payment.customer_phone}</span></td>
                    <td className="td font-semibold">{inr(payment.amount)}</td>
                    <td className="td">{pretty(payment.method)}</td>
                    <td className="td font-mono text-xs">{payment.gateway_payment_id || payment.reference || '—'}</td>
                    <td className="td">{date(payment.payment_date, true)}</td>
                    <td className="td"><span className={`badge ${statusTone(payment.status)}`}>{pretty(payment.status)}</span></td>
                    <td className="td"><div className="flex justify-end gap-1">
                      {payment.status === 'AWAITING_CONFIRMATION' && <>
                        <button className="btn-ghost px-2 py-1.5 text-emerald-600 hover:bg-emerald-50" onClick={() => setDialog({ type: 'confirm', payment })}><CheckCircle2 className="h-4 w-4" /> Confirm</button>
                        <button className="btn-ghost px-2 py-1.5 text-rose-600 hover:bg-rose-50" onClick={() => setDialog({ type: 'reject', payment })}><XCircle className="h-4 w-4" /> Reject</button>
                      </>}
                      {payment.status === 'CONFIRMED' && <button className="btn-ghost px-2 py-1.5 text-amber-600 hover:bg-amber-50" onClick={() => setDialog({ type: 'reverse', payment })}><RotateCcw className="h-4 w-4" /> Reverse</button>}
                      {payment.rejection_reason && <span className="max-w-[180px] truncate text-xs text-slate-400" title={payment.rejection_reason}>{payment.rejection_reason}</span>}
                    </div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Pagination page={data.page} pages={data.pages} total={data.total} onPage={setPage} />
      </Card>

      <Modal open={!!manual} onClose={() => setManual(null)} title="Record manual payment" subtitle="All manual payments require administrator confirmation">
        {manual && <div className="space-y-4">
          <Field label="Customer"><select className="input" value={manual.customer_id} onChange={(e) => setManual({ ...manual, customer_id: e.target.value })}><option value="">Select customer…</option>{customers.map((c) => <option key={c.id} value={c.id}>{c.name} · {inr(c.outstanding_balance)}</option>)}</select></Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Amount (₹)"><input className="input" type="number" min="0.01" step="0.01" value={manual.amount} onChange={(e) => setManual({ ...manual, amount: e.target.value })} /></Field>
            <Field label="Payment method"><select className="input" value={manual.method} onChange={(e) => setManual({ ...manual, method: e.target.value })}><option>CASH</option><option>BANK_TRANSFER</option><option>UPI</option><option>OTHER</option></select></Field>
          </div>
          <Field label="Transaction reference (optional)"><input className="input" value={manual.reference} onChange={(e) => setManual({ ...manual, reference: e.target.value })} placeholder="UTR / bank reference" /></Field>
          <Field label="Notes (optional)"><textarea className="input min-h-[70px]" value={manual.notes} onChange={(e) => setManual({ ...manual, notes: e.target.value })} /></Field>
          <div className="flex justify-end gap-2"><button className="btn-secondary" onClick={() => setManual(null)}>Cancel</button><button className="btn-primary" disabled={busy || !manual.customer_id || Number(manual.amount) <= 0} onClick={saveManual}>{busy ? 'Saving…' : 'Record payment'}</button></div>
        </div>}
      </Modal>

      <ConfirmDialog
        open={!!dialog}
        onClose={() => setDialog(null)}
        onConfirm={act}
        busy={busy}
        danger={dialog?.type !== 'confirm'}
        title={({ confirm: 'Confirm payment', reject: 'Reject payment', reverse: 'Reverse payment' })[dialog?.type] || 'Confirm'}
        description={dialog ? (
          dialog.type === 'confirm'
            ? `Approve ${inr(dialog.payment.amount)} from ${dialog.payment.customer_name}? The outstanding balance will be reduced and a confirmation message sent.`
            : dialog.type === 'reject'
              ? 'Rejecting this payment does not change the customer balance. Please provide a reason.'
              : 'This restores the amount to the outstanding balance and writes a reversal ledger entry. The original transaction is preserved.'
        ) : ''}
        confirmLabel={({ confirm: 'CONFIRM', reject: 'REJECT', reverse: 'REVERSE' })[dialog?.type] || 'Confirm'}
      >
        {dialog?.type !== 'confirm' && <div className="mt-4"><Field label="Reason"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Enter a clear reason" /></Field></div>}
      </ConfirmDialog>
    </div>
  )
}
