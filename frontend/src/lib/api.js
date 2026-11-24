import axios from 'axios'

const api = axios.create({ baseURL: '/api', timeout: 60000 })

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('essvee_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && !error.config?.url?.includes('/auth/login')) {
      localStorage.removeItem('essvee_token')
      if (window.location.pathname !== '/login') window.location.assign('/login')
    }
    const message =
      error.response?.data?.error?.message ||
      error.response?.data?.detail ||
      error.message ||
      'Something went wrong'
    error.userMessage = Array.isArray(message) ? JSON.stringify(message) : String(message)
    return Promise.reject(error)
  },
)

export const errMsg = (error) => error?.userMessage || error?.message || 'Request failed'

export async function download(path, payload = null, method = 'get') {
  const token = localStorage.getItem('essvee_token')
  const response = await axios({
    method,
    url: `/api${path}`,
    data: payload,
    responseType: 'blob',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  const disposition = response.headers['content-disposition'] || ''
  const match = disposition.match(/filename="?([^";]+)"?/)
  const filename = match?.[1] || `essvee-export-${Date.now()}.xlsx`
  const url = window.URL.createObjectURL(new Blob([response.data]))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
}

export default api
