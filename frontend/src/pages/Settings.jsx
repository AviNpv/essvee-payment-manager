import { useEffect, useState } from 'react'
import { Building2, CheckCircle2, CreditCard, FileSpreadsheet, MessageCircle, PlugZap, Save, Settings2, Smartphone, Trash2, XCircle } from 'lucide-react'
import api, { errMsg } from '../lib/api'
import { useToast } from '../lib/Toast'
import { Badge, Card, CardHeader, Field, Loading, PageHeader, Spinner, Toggle } from '../components/ui'

const sections = [
  { key: 'company', label: 'Company', icon: Building2 },
  { key: 'whatsapp', label: 'WhatsApp', icon: MessageCircle },
  { key: 'razorpay', label: 'Razorpay', icon: CreditCard },
  { key: 'upi', label: 'UPI', icon: Smartphone },
  { key: 'google', label: 'Google Sheets', icon: FileSpreadsheet },
  { key: 'reminder', label: 'Reminders', icon: Settings2 },
  { key: 'payment', label: 'Payments', icon: CreditCard },
]

const secretFields = new Set(['whatsapp_access_token', 'whatsapp_phone_number_id', 'whatsapp_waba_id', 'whatsapp_webhook_verify_token', 'whatsapp_app_secret', 'razorpay_key_secret', 'razorpay_webhook_secret', 'google_service_account_json'])

export default function Settings() {
  const [data, setData] = useState(null)
  const [section, setSection] = useState('company')
  const [form, setForm] = useState(null)
  const [busy, setBusy] = useState(false)
  const [test, setTest] = useState(null)
  const toast = useToast()

  const load = () => api.get('/settings').then((r) => { setData(r.data); setForm(structuredClone(r.data)) }).catch((e) => toast.error(errMsg(e)))
  useEffect(() => { load() }, [])

  const save = async () => {
    setBusy(true)
    try {
      if (section === 'reminder') await api.put('/settings/reminder', form.reminder)
      else if (section === 'payment') await api.put('/settings/payment', form.payment)
      else await api.put(`/settings/${section}`, form[section])
      toast.success('Settings saved')
      await load()
    } catch (e) { toast.error(errMsg(e)) } finally { setBusy(false) }
  }

  const clear = async (key) => {
    try { await api.post(`/settings/clear/${key}`); toast.success('Credential cleared'); load() }
    catch (e) { toast.error(errMsg(e)) }
  }

  const testConnection = async (provider) => {
    setBusy(true)
    try {
      const r = await api.post(`/settings/test/${provider}`)
      setTest({ provider, ...r.data })
      toast[r.data.success ? 'success' : 'error'](r.data.message)
      await load()
    } catch (e) { toast.error(errMsg(e)) } finally { setBusy(false) }
  }

  if (!data || !form) return <Loading label="Loading settings…" />

  const set = (group, key, value) => setForm({ ...form, [group]: { ...form[group], [key]: value } })

  const text = (group, key, label, type = 'text', hint = '') => (
    <Field key={key} label={label} hint={hint}>
      <div className="flex gap-2">
        <input className="input" type={type} value={form[group][key] ?? ''} onChange={(e) => set(group, key, e.target.value)} />
        {secretFields.has(key) && form[group][key] && (
          <button className="btn-ghost px-2" title="Clear credential" onClick={() => clear(key)}><Trash2 className="h-4 w-4 text-rose-500" /></button>
        )}
      </div>
    </Field>
  )

  return (
    <div className="space-y-5">
      <PageHeader title="Settings" subtitle="Company identity, integration credentials and automation rules" actions={<button className="btn-primary" onClick={save} disabled={busy}>{busy ? <Spinner /> : <Save className="h-4 w-4" />} Save changes</button>} />
      <div className="flex flex-wrap gap-1 border-b border-slate-200 dark:border-slate-800">
        {sections.map((item) => <button key={item.key} onClick={() => setSection(item.key)} className={`flex items-center gap-2 rounded-t-lg px-4 py-2.5 text-sm font-semibold transition ${section === item.key ? 'border-b-2 border-brand-600 text-brand-700 dark:text-brand-300' : 'text-slate-500 hover:text-slate-800 dark:hover:text-slate-200'}`}><item.icon className="h-4 w-4" /> {item.label}</button>)}
      </div>

      {section === 'company' && <Card><CardHeader title="Company settings" subtitle="Branding used across the app, messages and exports" /><div className="grid gap-4 p-5 sm:grid-cols-2">
        {text('company', 'company_name', 'Company name')}
        {text('company', 'company_logo', 'Logo URL (optional)', 'text', 'Leave blank to use the EA monogram.')}
        {text('company', 'contact_number', 'Contact number')}
        {text('company', 'business_email', 'Business email')}
        {text('company', 'address', 'Business address')}
        {text('company', 'currency', 'Currency')}
        {text('company', 'timezone', 'Timezone')}
      </div></Card>}

      {section === 'whatsapp' && <>
        <Card><CardHeader title="WhatsApp Cloud API credentials" subtitle="Stored encrypted on the backend — never sent to browsers in plain text" action={<button className="btn-secondary" onClick={() => testConnection('whatsapp')} disabled={busy}><PlugZap className="h-4 w-4" /> Test connection</button>} />
          <div className="grid gap-4 p-5 sm:grid-cols-2">
            {text('whatsapp', 'whatsapp_access_token', 'Meta access token', 'text', 'Paste a new token to replace the saved value.')}
            {text('whatsapp', 'whatsapp_phone_number_id', 'Phone Number ID')}
            {text('whatsapp', 'whatsapp_waba_id', 'WhatsApp Business Account ID')}
            {text('whatsapp', 'whatsapp_webhook_verify_token', 'Webhook verification token')}
            {text('whatsapp', 'whatsapp_app_secret', 'Meta application secret', 'text', 'Used to verify X-Hub-Signature-256 on webhooks.')}
            {text('whatsapp', 'whatsapp_api_version', 'Graph API version')}
          </div>
        </Card>
        <Card><CardHeader title="Approved message templates" subtitle="Template names must match Meta Business Manager exactly" /><div className="grid gap-4 p-5 sm:grid-cols-3">
          {text('whatsapp', 'reminder_template', 'Payment reminder template')}
          {text('whatsapp', 'confirmation_template', 'Payment confirmation template')}
          {text('whatsapp', 'partial_template', 'Partial payment template')}
        </div></Card>
      </>}

      {section === 'razorpay' && <>
        <Card><CardHeader title="Razorpay credentials" subtitle="Use TEST MODE credentials during development" action={<button className="btn-secondary" onClick={() => testConnection('razorpay')} disabled={busy}><PlugZap className="h-4 w-4" /> Test connection</button>} />
          <div className="grid gap-4 p-5 sm:grid-cols-2">
            {text('razorpay', 'razorpay_key_id', 'Key ID')}
            {text('razorpay', 'razorpay_key_secret', 'Key Secret')}
            {text('razorpay', 'razorpay_webhook_secret', 'Webhook secret')}
            {text('razorpay', 'razorpay_callback_url', 'Payment link callback URL (optional)')}
            <Field label="Mode"><select className="input" value={form.razorpay.razorpay_mode} onChange={(e) => set('razorpay', 'razorpay_mode', e.target.value)}><option value="test">TEST MODE</option><option value="live">LIVE MODE</option></select></Field>
          </div>
        </Card>
        <Card><CardHeader title="Webhook endpoint" subtitle="Configure this URL in the Razorpay dashboard" /><div className="p-5"><code className="block rounded-lg bg-slate-100 p-3 text-xs dark:bg-slate-800">https://your-domain.com/api/webhooks/razorpay</code><p className="mt-3 text-xs text-slate-500">Subscribe to <strong>payment.captured</strong>, <strong>payment.failed</strong> and <strong>payment_link.paid</strong> events. Payments always remain AWAITING_CONFIRMATION until approved by an administrator.</p></div></Card>
      </>}

      {section === 'upi' && <>
        <Card><CardHeader title="UPI payment setup" subtitle="Pay Now opens the customer's UPI app chooser" action={<button className="btn-secondary" onClick={() => testConnection('upi')} disabled={busy}><PlugZap className="h-4 w-4" /> Check setup</button>} />
          <div className="grid gap-4 p-5 sm:grid-cols-2">
            <Field label="Payment provider" hint="UPI opens the phone's UPI apps. Razorpay uses hosted Payment Links.">
              <select className="input" value={form.upi.payment_provider} onChange={(e) => set('upi', 'payment_provider', e.target.value)}>
                <option value="UPI">UPI app chooser</option>
                <option value="RAZORPAY">Razorpay Payment Link</option>
              </select>
            </Field>
            {text('upi', 'upi_vpa', 'UPI ID / VPA', 'text', 'Example: yourname@bank — must be a real collecting account.')}
            {text('upi', 'upi_payee_name', 'Payee name', 'text', 'Shown inside the UPI app.')}
            {text('upi', 'public_base_url', 'Public base URL', 'text', 'Use https:// for Meta WhatsApp buttons.')}
            <Field label="Amount in UPI app" hint="Customer enters the amount is the default you selected.">
              <select className="input" value={form.upi.upi_amount_mode} onChange={(e) => set('upi', 'upi_amount_mode', e.target.value)}>
                <option value="CUSTOMER_ENTERS">Customer enters amount</option>
                <option value="FIXED_OUTSTANDING">Pre-fill outstanding amount</option>
              </select>
            </Field>
          </div>
        </Card>
        <Card><CardHeader title="How Pay Now works" subtitle="WhatsApp button → HTTPS redirect → UPI app" /><div className="space-y-2 p-5 text-sm text-slate-600 dark:text-slate-300">
          <p>1. The approved WhatsApp template button opens <code className="rounded bg-slate-100 px-1 py-0.5 text-xs dark:bg-slate-800">{form.upi.public_base_url || 'https://your-domain.com'}/pay/…</code>.</p>
          <p>2. That page immediately opens <code className="rounded bg-slate-100 px-1 py-0.5 text-xs dark:bg-slate-800">upi://pay?…</code>, so the phone shows GPay, PhonePe, Paytm and other installed UPI apps.</p>
          <p>3. With “Customer enters amount”, the UPI app does not force an amount. The customer pays what they want and shares the UTR; your team confirms the real payment in Payments.</p>
          <p className="text-xs text-slate-400">Need Razorpay instead? Choose “Razorpay Payment Link” above and configure Razorpay credentials.</p>
        </div></Card>
      </>}

      {section === 'google' && <>
        <Card><CardHeader title="Google Sheets connection" subtitle="Service account JSON is encrypted at rest — never returned to the browser in plain text" /><div className="space-y-4 p-5">
          <div className="flex flex-wrap gap-2 text-xs">
            <Badge tone={data.configured?.google_sheets ? 'green' : 'amber'}>{data.configured?.google_sheets ? 'Service account configured' : 'Service account missing'}</Badge>
            <Badge tone={data.configured?.google_spreadsheet ? 'green' : 'amber'}>{data.configured?.google_spreadsheet ? 'Spreadsheet linked' : 'Spreadsheet not linked'}</Badge>
          </div>
          {text('google', 'google_spreadsheet_id', 'Spreadsheet ID or share URL', 'text', 'Paste the full Google Sheets URL — the ID is extracted automatically.')}
          <Field label="Service account JSON" hint="Prefer storing this as GOOGLE_SERVICE_ACCOUNT_JSON in Render. Pasting here stores it encrypted in the database.">
            <div className="flex gap-2">
              <textarea className="input min-h-[120px] font-mono text-xs" value={form.google.google_service_account_json || ''} onChange={(e) => set('google', 'google_service_account_json', e.target.value)} placeholder='{"type":"service_account",…}' />
              {form.google.google_service_account_json && (
                <button className="btn-ghost px-2" title="Clear credential" onClick={() => clear('google_service_account_json')}><Trash2 className="h-4 w-4 text-rose-500" /></button>
              )}
            </div>
          </Field>
          <p className="text-xs text-slate-500">Share the spreadsheet with the service account email shown on the <strong>Google Sheets</strong> page as <strong>Viewer</strong>. The Tuesday, Thursday and Saturday tabs need <strong>Name</strong> and <strong>Amount</strong>; the Customers tab needs <strong>Name</strong> and <strong>Mobile</strong>.</p>
        </div></Card>
      </>}

      {section === 'reminder' && <Card><CardHeader title="Reminder automation" subtitle="Balance-based scheduling — there are no invoice due dates" /><div className="divide-y divide-slate-100 px-5 dark:divide-slate-800">
        <Toggle checked={form.reminder.automation_enabled} onChange={(v) => set('reminder', 'automation_enabled', v)} label="Automation enabled" description="Pause or resume all automated reminders globally." />
        <div className="grid gap-4 py-4 sm:grid-cols-2">
          {[
            ['frequency_days', 'Repeat every (days)'], ['first_reminder_after_days', 'First reminder after (days)'],
            ['max_reminders', 'Maximum reminders per customer'], ['daily_limit', 'Daily message limit'],
            ['quiet_start', 'Quiet hours start (IST)'], ['quiet_end', 'Quiet hours end (IST)'],
            ['max_retry_attempts', 'Maximum retry attempts'],
          ].map(([key, label]) => <Field key={key} label={label}><input className="input" type={key.includes('quiet') ? 'time' : 'number'} min="0" value={form.reminder[key]} onChange={(e) => set('reminder', key, key.includes('quiet') ? e.target.value : Number(e.target.value))} /></Field>)}
        </div>
        <Toggle checked={form.reminder.retry_failed_messages} onChange={(v) => set('reminder', 'retry_failed_messages', v)} label="Retry failed messages" description="Automatically retry temporary WhatsApp delivery failures." />
      </div></Card>}

      {section === 'payment' && <Card><CardHeader title="Payment settings" subtitle="Approval workflow and payment link behaviour" /><div className="divide-y divide-slate-100 px-5 dark:divide-slate-800">
        <Toggle checked={form.payment.manual_payment_approval} onChange={(v) => set('payment', 'manual_payment_approval', v)} label="Manual payment approval" description="Every manual payment must be confirmed by an administrator." />
        <Toggle checked={form.payment.allow_partial_payments} onChange={(v) => set('payment', 'allow_partial_payments', v)} label="Allow partial payments" description="Razorpay payment links will accept partial payments when enabled." />
        <div className="grid gap-4 py-4 sm:grid-cols-2">
          <Field label="Payment link expiry (days)"><input className="input" type="number" min="1" value={form.payment.link_expiry_days} onChange={(e) => set('payment', 'link_expiry_days', Number(e.target.value))} /></Field>
          <Field label="Minimum payment amount (₹)"><input className="input" type="number" min="0" step="1" value={form.payment.minimum_payment_amount} onChange={(e) => set('payment', 'minimum_payment_amount', Number(e.target.value))} /></Field>
        </div>
      </div></Card>}

      {test && <div className={`flex items-center gap-2 rounded-lg p-3 text-sm ${test.success ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300' : 'bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300'}`}>{test.success ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}{test.message}</div>}
      <div className="flex justify-end"><button className="btn-primary" onClick={save} disabled={busy}><Save className="h-4 w-4" /> Save {section} settings</button></div>
    </div>
  )
}
