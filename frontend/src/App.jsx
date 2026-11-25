import { Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Customers from './pages/Customers'
import Reports from './pages/Reports'
import Settings from './pages/Settings'
import Sheets from './pages/Sheets'
import SystemPage from './pages/System'

function Protected({ children }) {
  const token = localStorage.getItem('essvee_token')
  return token ? children : <Navigate to="/login" replace />
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<Protected><Layout /></Protected>}>
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/customers" element={<Customers />} />
        <Route path="/sheets" element={<Sheets />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/system" element={<SystemPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  )
}
