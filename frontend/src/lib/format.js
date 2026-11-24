export const inr = (value, opts = {}) => {
  const number = Number(value || 0)
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: opts.decimals === false ? 0 : 2,
    minimumFractionDigits: opts.decimals === false ? 0 : 2,
  }).format(number)
}

export const num = (value) => new Intl.NumberFormat('en-IN').format(Number(value || 0))

export const date = (value, withTime = false) => {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  return new Intl.DateTimeFormat('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    ...(withTime ? { hour: '2-digit', minute: '2-digit' } : {}),
  }).format(d)
}

export const initials = (name = '') =>
  name
    .split(' ')
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join('')

export const statusTone = (status = '') => {
  const s = status.toUpperCase()
  if (['CONFIRMED', 'DELIVERED', 'READ', 'ACTIVE', 'OPT_IN', 'COMPLETED'].includes(s))
    return 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300'
  if (['AWAITING_CONFIRMATION', 'QUEUED', 'SENT', 'PARTIALLY_PAID'].includes(s))
    return 'bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300'
  if (['FAILED', 'REJECTED', 'OPT_OUT', 'CANCELLED', 'REVERSED'].includes(s))
    return 'bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300'
  return 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300'
}

export const pretty = (value = '') =>
  String(value)
    .replaceAll('_', ' ')
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase())
