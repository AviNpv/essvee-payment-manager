import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Archive, BellRing, Ban, ChevronDown, CreditCard, Eye, FileText, Link2,
  MoreHorizontal, Pencil, Plus, Receipt, Search, Trash2, UserPlus, Users,
} from 'lucide-react'
import api, { errMsg } from '../lib/api'
import { useToast } from '../lib/Toast'
import { date, inr, initials, pretty, statusTone } from '../lib/format'
import {
  Badge, Card, ConfirmDialog, EmptyState, Field, Loading, Modal, PageHeader,
  Pagination, SearchInput,
} from '../components/ui'

const blank = { name: '', whatsapp_number: '', email: '', opening_balance: '0', status: 'ACTIVE', consent_status: 'OPT_IN', notes: '' }

export default function Customers() {
  const [params, setParams] = useSearchParams()
  const [rows, setRows] = useState([])
  const [meta, setMeta] = useState({ total: 0, page: 1, pages: 1 })
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState(params.get('status') || '')
  const [outstanding, setOutstanding] = useState(params.get('outstanding') || 'all')
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [form, setForm] = useState(null)
  const [editing, setEditing] = useState(null)
  const [busy, setBusy] = useState(false)
  const [detail, setDetail] = useState(null)
  const [history, setHistory] = useState(null)
  const [action, setAction] = useState(null)
  const [amount, setAmount] = useState('')
  const [reference, setReference] = useState('')
  const [notes, setNotes] = useState('')
  const toast = useToast()

  const load = () => {
    setLoading(true)
    const query = new URLSearchParams({ page, page_size: 25 })
    if (search) query.set('search', search)
    if (status) query.set('status', status)
    if (outstanding && outstanding !== 'all') query.set('outstanding', outstanding)
    api.get(`/customers?${query}`)
      .then((r) => { setRows(r.data.items); setMeta(r.data) })
      .catch((e) => toast.error(errMsg(e)))
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [page, status, outstanding, search])

  useEffect(() => {
    if (params.get('new') === '1') { setForm({ ...blank }); setEditing(null) }
  }, [params])

  useEffect(() => {
    const timer = setTimeout(() => { if (search) setPage(1); load() }, 300)
    return () => clearTimeout(timer)
  }, [search])

  const saveCustomer = async () => {
    setBusy(true)
    try {
      const payload = { ...form, opening_balance: Number(form.opening_balance || 0) }
      if (editing) await api.put(`/customers/${editing.id}`, payload)
      else await api.post('/customers', payload)
      toast.success(editing ? 'Customer updated' : 'Customer added')
      setForm(null); setEditing(null); load()
      setParams({})
    } catch (e) { toast.error(errMsg(e)) } finally { setBusy(false) }
  }

  const openDetail = async (customer) => {
    setDetail(customer); setHistory(null)
    try { const r = await api.get(`/customers/${customer.id}/history`); setHistory(r.data) }
    catch (e) { toast.error(errMsg(e)) }
  }

  const runAction = async () => {
    if (!action) return
    setBusy(true)
    try {
      const customer = action.customer
      if (action.type === 'reminder') {
        const r = await api.post('/whatsapp/reminders/send', { customer_id: customer.id })
        toast.success(r.data.status === 'FAILED' ? `Message failed: ${r.data.error}` : 'Reminder sent')
      } else if (action.type === 'link') {
        await api.post('/payment-links/generate', { customer_id: customer.id })
        toast.success('Payment link ready'); window.open('/#/customers', '_self')
      } else if (action.type === 'payment') {
        await api.post('/payments/manual', { customer_id: customer.id, amount: Number(amount), method: 'CASH', reference, notes })
        toast.success('Payment recorded — awaiting confirmation')
      } else if (action.type === 'adjust') {
        await api.post(`/customers/${customer.id}/adjustments`, { amount: Number(amount), transaction_type: 'ADJUSTMENT', description: notes || 'Manual adjustment' })
        toast.success('Balance adjusted')
      } else if (action.type === 'exclude') {
        await api.post(`/customers/${customer.id}/toggle-exclusion`)
        toast.success('Reminder preference updated')
      } else if (action.type === 'archive') {
        await api.post(`/customers/${customer.id}/archive`)
        toast.success('Customer archived')
      } else if (action.type === 'delete') {
        await api.delete(`/customers/${customer.id}`)
        toast.success('Customer deleted')
      } else if (action.type === 'consent') {
        await api.put(`/customers/${customer.id}`, { consent_status: customer.consent_status === 'OPT_IN' ? 'OPT_OUT' : 'OPT_IN' })
        toast.success('Consent updated')
      }
      setAction(null); setAmount(''); setReference(''); setNotes(''); load()
      if (detail) openDetail({ ...customer })
    } catch (e) { toast.error(errMsg(e)) } finally { setBusy(false) }
  }

  const needsAmount = ['payment', 'adjust'].includes(action?.type)

  return (
    <div className="space-y-5">
      <PageHeader title="Customers" subtitle="Manage customer records, WhatsApp consent and outstanding balances" actions={
        <>
          <button className="btn-secondary" onClick={() => {
            api.get('/excel/template', { responseType: 'blob' }).then((r) => {
              const url = URL.createObjectURL(new Blob([r.data])); const a = document.createElement('a')
              a.href = url; a.download = 'essvee_customer_template.xlsx'; a.click(); URL.revokeObjectURL(url)
            })
          }}>Template</button>
          <button className="btn-primary" onClick={() => { setEditing(null); setForm({ ...blank }); setParams({}) }}><Plus className="h-4 w-4" /> Add customer</button>
        </>
      } />

      <Card>
        <div className="flex flex-wrap items-center gap-3 p-4">
          <div className="flex-1"><SearchInput value={search} onChange={(v) => { setSearch(v); setPage(1) }} placeholder="Search name or WhatsApp number…" /></div>
          <select className="input w-auto min-w-[140px]" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1) }}>
            <option value="">All statuses</option><option>ACTIVE</option><option>INACTIVE</option><option>ARCHIVED</option>
          </select>
          <select className="input w-auto min-w-[150px]" value={outstanding} onChange={(e) => { setOutstanding(e.target.value); setPage(1) }}>
            <option value="all">All balances</option><option value="pending">Has outstanding</option><option value="zero">Zero balance</option>
          </select>
        </div>
        {loading ? <Loading /> : rows.length === 0 ? (
          <EmptyState icon={Users} title="No customers found" description="Add a customer manually or import an Excel file to get started." action={<button className="btn-primary" onClick={() => setForm({ ...blank })}><UserPlus className="h-4 w-4" /> Add customer</button>} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px]">
              <thead className="bg-slate-50/70 dark:bg-slate-800/50"><tr>
                <th className="th">Customer</th><th className="th">WhatsApp</th><th className="th">Outstanding</th>
                <th className="th">Total paid</th><th className="th">Last payment</th><th className="th">Last reminder</th>
                <th className="th">Status</th><th className="th text-right">Actions</th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {rows.map((customer) => (
                  <tr key={customer.id} className="hover:bg-slate-50/60 dark:hover:bg-slate-800/30">
                    <td className="td">
                      <button className="flex items-center gap-3 text-left" onClick={() => openDetail(customer)}>
                        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-50 text-xs font-bold text-brand-700 dark:bg-brand-500/15 dark:text-brand-300">{initials(customer.name)}</span>
                        <span><span className="block font-semibold">{customer.name}</span><span className="text-xs text-slate-400">{customer.email || '—'}</span></span>
                      </button>
                    </td>
                    <td className="td font-mono text-xs">{customer.whatsapp_number}</td>
                    <td className="td"><span className={`font-semibold ${Number(customer.outstanding_balance) > 0 ? 'text-rose-600' : 'text-emerald-600'}`}>{inr(customer.outstanding_balance)}</span></td>
                    <td className="td">{inr(customer.total_paid)}</td>
                    <td className="td">{date(customer.last_payment_at)}</td>
                    <td className="td">{date(customer.last_reminder_at)}</td>
                    <td className="td"><div className="flex flex-wrap gap-1"><span className={`badge ${statusTone(customer.status)}`}>{pretty(customer.status)}</span>{customer.exclude_from_reminders && <Badge tone="amber">Excluded</Badge>}</div></td>
                    <td className="td"><div className="flex justify-end gap-0.5">
                      <button className="btn-ghost p-1.5" title="View history" onClick={() => openDetail(customer)}><Eye className="h-4 w-4" /></button>
                      <button className="btn-ghost p-1.5" title="Send reminder" onClick={() => setAction({ type: 'reminder', customer })}><BellRing className="h-4 w-4" /></button>
                      <button className="btn-ghost p-1.5" title="Generate payment link" onClick={async () => {
                        try { const r = await api.post('/payment-links/generate', { customer_id: customer.id }); window.open(r.data.url, '_blank', 'noopener'); toast.success('Payment link opened') } catch (e) { toast.error(errMsg(e)) }
                      }}><Link2 className="h-4 w-4" /></button>
                      <button className="btn-ghost p-1.5" title="Record payment" onClick={() => setAction({ type: 'payment', customer })}><Receipt className="h-4 w-4" /></button>
                      <button className="btn-ghost p-1.5" title="Edit" onClick={() => { setEditing(customer); setForm({ ...customer, opening_balance: customer.opening_balance }) }}><Pencil className="h-4 w-4" /></button>
                      <button className="btn-ghost p-1.5" title="More actions" onClick={() => setAction({ type: 'exclude', customer })}><MoreHorizontal className="h-4 w-4" /></button>
                    </div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Pagination page={meta.page} pages={meta.pages} total={meta.total} onPage={setPage} />
      </Card>

      <Modal open={!!form} onClose={() => setForm(null)} title={editing ? 'Edit customer' : 'Add customer'} subtitle="Indian mobile numbers are normalised to international format">
        {form && <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Customer name"><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Rahul Sharma" /></Field>
            <Field label="WhatsApp number"><input className="input" value={form.whatsapp_number} onChange={(e) => setForm({ ...form, whatsapp_number: e.target.value })} placeholder="9876543210 or +919876543210" /></Field>
            <Field label="Email (optional)"><input className="input" value={form.email || ''} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="customer@example.com" /></Field>
            <Field label={editing ? 'Opening balance (read-only)' : 'Opening outstanding balance'} hint={editing ? 'Use a balance adjustment to change this.' : '₹ amount this customer owes.'}>
              <input className="input" type="number" min="0" step="0.01" disabled={!!editing} value={form.opening_balance} onChange={(e) => setForm({ ...form, opening_balance: e.target.value })} />
            </Field>
            <Field label="Status"><select className="input" value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}><option>ACTIVE</option><option>INACTIVE</option><option>ARCHIVED</option></select></Field>
            <Field label="WhatsApp consent"><select className="input" value={form.consent_status} onChange={(e) => setForm({ ...form, consent_status: e.target.value })}><option>OPT_IN</option><option>OPT_OUT</option><option>UNKNOWN</option></select></Field>
          </div>
          <Field label="Notes (optional)"><textarea className="input min-h-[70px]" value={form.notes || ''} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field>
          <div className="flex justify-end gap-2 pt-2"><button className="btn-secondary" onClick={() => setForm(null)}>Cancel</button><button className="btn-primary" onClick={saveCustomer} disabled={busy || form.name.trim().length < 2 || !form.whatsapp_number}>{busy ? 'Saving…' : 'Save customer'}</button></div>
        </div>}
      </Modal>

      <Modal open={!!detail} onClose={() => setDetail(null)} title={detail?.name || 'Customer'} subtitle={detail?.whatsapp_number} size="lg">
        {detail && <div className="space-y-5">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[['Outstanding', inr(detail.outstanding_balance)], ['Total paid', inr(detail.total_paid)], ['Charges', inr(detail.total_charges)], ['Reminders', detail.reminder_count]].map(([label, value]) => (
              <div key={label} className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/60"><p className="text-[11px] uppercase tracking-wide text-slate-500">{label}</p><p className="mt-1 text-sm font-bold">{value}</p></div>
            ))}
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn-secondary" onClick={() => setAction({ type: 'reminder', customer: detail })}><BellRing className="h-4 w-4" /> Send reminder</button>
            <button className="btn-secondary" onClick={async () => { try { const r = await api.post('/payment-links/generate', { customer_id: detail.id }); window.open(r.data.url, '_blank', 'noopener'); toast.success('Payment link opened') } catch (e) { toast.error(errMsg(e)) } }}><Link2 className="h-4 w-4" /> Payment link</button>
            <button className="btn-secondary" onClick={() => setAction({ type: 'payment', customer: detail })}><CreditCard className="h-4 w-4" /> Record payment</button>
            <button className="btn-secondary" onClick={() => setAction({ type: 'adjust', customer: detail })}><FileText className="h-4 w-4" /> Adjust balance</button>
            <button className="btn-secondary" onClick={() => setAction({ type: 'consent', customer: detail })}>{detail.consent_status === 'OPT_IN' ? <Ban className="h-4 w-4" /> : <UserPlus className="h-4 w-4" />} {detail.consent_status === 'OPT_IN' ? 'Opt out' : 'Opt in'}</button>
            <button className="btn-secondary" onClick={() => setAction({ type: 'exclude', customer: detail })}><Ban className="h-4 w-4" /> {detail.exclude_from_reminders ? 'Include' : 'Exclude'} reminders</button>
            <button className="btn-secondary" onClick={() => setAction({ type: 'archive', customer: detail })}><Archive className="h-4 w-4" /> Archive</button>
          </div>
          <div>
            <h4 className="mb-2 text-sm font-semibold">Financial & message history</h4>
            {!history ? <Loading /> : (
              <div className="max-h-80 overflow-y-auto rounded-lg border border-slate-200 dark:border-slate-700">
                {[...history.transactions.map((t) => ({ ...t, kind: 'Ledger' })), ...history.payments.map((p) => ({ ...p, kind: 'Payment' })), ...history.messages.map((m) => ({ ...m, kind: 'Message' }))]
                  .sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
                  .map((item, index) => (
                    <div key={`${item.kind}-${item.id}-${index}`} className="flex items-center justify-between gap-3 border-b border-slate-100 px-3 py-2 text-xs last:border-0 dark:border-slate-800">
                      <div><span className="font-semibold">{item.kind}</span> · {item.description || item.method || item.template_name || item.message_type}</div>
                      <div className="text-right"><span className="font-semibold">{item.amount ? inr(item.amount) : ''}</span><span className={`ml-2 badge ${statusTone(item.status || item.effect)}`}>{pretty(item.status || item.effect || '')}</span></div>
                    </div>
                  ))}
                {!history.transactions.length && !history.payments.length && !history.messages.length && <EmptyState title="No history yet" />}
              </div>
            )}
          </div>
        </div>}
      </Modal>

      <ConfirmDialog
        open={!!action}
        onClose={() => setAction(null)}
        onConfirm={runAction}
        busy={busy}
        danger={action?.type === 'delete' || action?.type === 'archive'}
        title={({
          reminder: 'Send WhatsApp reminder', link: 'Generate payment link', payment: 'Record manual payment',
          adjust: 'Adjust outstanding balance', exclude: 'Change reminder exclusion', archive: 'Archive customer',
          delete: 'Delete customer', consent: 'Change WhatsApp consent',
        })[action?.type] || 'Confirm action'}
        description={({
          reminder: 'A unique Razorpay link will be reused or created and an approved template message sent to this customer.',
          link: 'Create a fresh Razorpay payment link for the current outstanding balance?',
          payment: 'Record a payment. It will remain AWAITING_CONFIRMATION until an administrator approves it.',
          adjust: 'Create a ledger adjustment entry for this customer.',
          exclude: 'Toggle whether this customer is excluded from automated reminders.',
          archive: 'Archived customers stop receiving reminders. Financial history is preserved.',
          delete: 'Only customers without financial history can be deleted.',
          consent: 'Toggle WhatsApp consent for this customer.',
        })[action?.type] || ''}
        confirmLabel="Confirm"
      >
        {needsAmount && <div className="mt-4 space-y-3">
          <Field label="Amount (₹)"><input className="input" type="number" min="0.01" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>
          {action.type === 'payment' && <Field label="Reference (optional)"><input className="input" value={reference} onChange={(e) => setReference(e.target.value)} placeholder="UTR / receipt number" /></Field>}
          <Field label="Notes"><input className="input" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder={action.type === 'adjust' ? 'Reason for adjustment' : 'Optional'} /></Field>
        </div>}
      </ConfirmDialog>
    </div>
  )
}
