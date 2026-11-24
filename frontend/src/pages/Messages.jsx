import { useEffect, useState } from 'react'
import { MessageSquare, RefreshCw, Search } from 'lucide-react'
import api, { errMsg } from '../lib/api'
import { useToast } from '../lib/Toast'
import { date, pretty, statusTone } from '../lib/format'
import { Card, EmptyState, Loading, PageHeader, Pagination, SearchInput } from '../components/ui'

export default function Messages() {
  const [data, setData] = useState({ items: [], total: 0, pages: 1, page: 1 })
  const [status, setStatus] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState(null)
  const toast = useToast()

  const load = () => {
    setLoading(true)
    const query = new URLSearchParams({ page, page_size: 25 })
    if (status) query.set('status', status)
    if (search) query.set('search', search)
    api.get(`/whatsapp/messages?${query}`).then((r) => setData(r.data)).catch((e) => toast.error(errMsg(e))).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [status, search, page])

  const retry = async (message) => {
    try {
      await api.post(`/whatsapp/messages/${message.id}/retry`)
      toast.success('Retry attempted'); load()
    } catch (e) { toast.error(errMsg(e)) }
  }

  return (
    <div className="space-y-5">
      <PageHeader title="WhatsApp messages" subtitle="Delivery tracking, failures and opt-out replies" />
      <Card>
        <div className="flex flex-wrap items-center gap-3 p-4">
          <div className="flex-1"><SearchInput value={search} onChange={(v) => { setSearch(v); setPage(1) }} placeholder="Search phone or message content…" /></div>
          <select className="input w-auto" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1) }}>
            <option value="">All statuses</option><option>QUEUED</option><option>SENT</option><option>DELIVERED</option><option>READ</option><option>FAILED</option>
          </select>
        </div>
        {loading ? <Loading /> : data.items.length === 0 ? <EmptyState icon={MessageSquare} title="No messages found" /> : (
          <div className="overflow-x-auto"><table className="w-full min-w-[900px]"><thead className="bg-slate-50/70 dark:bg-slate-800/50"><tr><th className="th">Customer</th><th className="th">Phone</th><th className="th">Type</th><th className="th">Template</th><th className="th">Status</th><th className="th">Sent at</th><th className="th text-right">Actions</th></tr></thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">{data.items.map((message) => <tr key={message.id} className="hover:bg-slate-50/60 dark:hover:bg-slate-800/30">
              <td className="td font-medium">{message.customer_name || '—'}</td><td className="td font-mono text-xs">{message.phone_number}</td>
              <td className="td">{pretty(message.message_type)}</td><td className="td">{message.template_name || '—'}</td>
              <td className="td"><span className={`badge ${statusTone(message.status)}`}>{pretty(message.status)}</span></td>
              <td className="td">{date(message.created_at, true)}</td>
              <td className="td text-right"><div className="flex justify-end gap-1"><button className="btn-ghost px-2 py-1 text-xs" onClick={() => setSelected(message)}>Details</button>{message.status === 'FAILED' && <button className="btn-ghost px-2 py-1 text-xs text-brand-600" onClick={() => retry(message)}><RefreshCw className="h-3.5 w-3.5" /> Retry</button>}</div></td>
            </tr>)}</tbody></table></div>
        )}
        <Pagination page={data.page} pages={data.pages} total={data.total} onPage={setPage} />
      </Card>

      {selected && <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 p-4" onClick={() => setSelected(null)}>
        <div className="card w-full max-w-xl p-5" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-start justify-between"><h3 className="font-semibold">Message details</h3><button onClick={() => setSelected(null)} className="text-slate-400">✕</button></div>
          <div className="mt-4 space-y-3 text-sm">
            <p><span className="text-slate-500">Status:</span> {pretty(selected.status)}</p>
            <p><span className="text-slate-500">Provider ID:</span> <span className="font-mono text-xs">{selected.provider_message_id || '—'}</span></p>
            <p><span className="text-slate-500">Retries:</span> {selected.retry_count}</p>
            {selected.error && <p className="rounded-lg bg-rose-50 p-3 text-xs text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">{selected.error}</p>}
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-100 p-3 text-xs dark:bg-slate-800">{selected.body_preview}</pre>
          </div>
        </div>
      </div>}
    </div>
  )
}
