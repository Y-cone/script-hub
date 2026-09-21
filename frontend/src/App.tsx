import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import AppLayout from './components/AppLayout'
import ScriptLibrary from './pages/ScriptLibrary'
import ScriptDetail from './pages/ScriptDetail'
import RunHistory from './pages/RunHistory'
import SystemInfo from './pages/SystemInfo'
import Schedules from './pages/Schedules'
import Devices from './pages/Devices'
import TerminalPage from './pages/TerminalPage'
import { useDesktopTheme } from './theme/desktopTheme'
import { useGlobalShortcuts } from './hooks/useGlobalShortcuts'
import './styles/desktop.css'

export default function App() {
  const { themeConfig } = useDesktopTheme()
  useGlobalShortcuts()

  return (
    <ConfigProvider locale={zhCN} theme={themeConfig}>
      <BrowserRouter>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/" element={<ScriptLibrary />} />
            <Route path="/scripts/:id" element={<ScriptDetail />} />
            <Route path="/history" element={<RunHistory />} />
            <Route path="/system" element={<SystemInfo />} />
            <Route path="/schedules" element={<Schedules />} />
            <Route path="/devices" element={<Devices />} />
            <Route path="/terminal" element={<TerminalPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  )
}
