import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles/window.css'   // R-1：无装饰窗口自绘圆角（放在 App 之后 → 覆盖 desktop.css/proto.css 的底色）

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
