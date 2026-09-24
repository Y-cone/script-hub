import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { ConfigProvider, message } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import DesktopShell from './components/desktop/DesktopShell'
import ScriptWorkspace from './components/desktop/ScriptWorkspace'
import ScriptLibrary from './pages/ScriptLibrary'
import RunHistory from './pages/RunHistory'
import SystemInfo from './pages/SystemInfo'
import Schedules from './pages/Schedules'
import Devices from './pages/Devices'
import { useDesktopTheme } from './theme/desktopTheme'
import { useGlobalShortcuts } from './hooks/useGlobalShortcuts'
import './styles/desktop.css'
import './styles/proto.css'   // V5-G：原型样式层（1:1 移植 mockup CSS，作用域 .desktop-mode）

// 批次 AZ（用户真机验收②）：「保存配置」等 message 提示原先紧贴顶栏（antd 默认 top:8，实测 notice
// rect.top=8，落在 40px 顶栏区内）。下移到中上：1280×800 窗口下 topbar+tab 条占顶部 130px、窗口中线 400，
// 取 240 = 两者之间偏上（用户建议区间 200~260 的中点），既不遮顶栏也不居中。
// message.config 是全局单例配置：模块顶层只调一次（勿在各组件内调）。
message.config({ top: 240 })

function AppRoutes() {
  return (
    <BrowserRouter>
      <Routes>
        {/* V5-G：桌面形态用 DesktopShell（顶栏导航 + 全局终端底栏） */}
        <Route element={<DesktopShell />}>
          <Route path="/" element={<ScriptLibrary />} />
          {/* V5-G：桌面形态脚本详情用 ScriptWorkspace（双栏 + 参数栏），内嵌 pages/ScriptDetail 复用逻辑 */}
          <Route path="/scripts/:id" element={<ScriptWorkspace />} />
          <Route path="/history" element={<RunHistory />} />
          <Route path="/system" element={<SystemInfo />} />
          <Route path="/schedules" element={<Schedules />} />
          <Route path="/devices" element={<Devices />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default function App() {
  const { themeConfig } = useDesktopTheme()
  useGlobalShortcuts()

  return (
    <ConfigProvider locale={zhCN} theme={themeConfig}>
      <AppRoutes />
    </ConfigProvider>
  )
}
