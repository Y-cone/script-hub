import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import AppLayout from './components/AppLayout'
import ScriptLibrary from './pages/ScriptLibrary'
import ScriptDetail from './pages/ScriptDetail'
import RunHistory from './pages/RunHistory'
import SystemInfo from './pages/SystemInfo'
import Schedules from './pages/Schedules'

export default function App() {
  return (
    <ConfigProvider locale={zhCN}>
      <BrowserRouter>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/" element={<ScriptLibrary />} />
            <Route path="/scripts/:id" element={<ScriptDetail />} />
            <Route path="/history" element={<RunHistory />} />
            <Route path="/system" element={<SystemInfo />} />
            <Route path="/schedules" element={<Schedules />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  )
}
