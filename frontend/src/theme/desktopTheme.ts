/**
 * V5-F F1 桌面设计令牌（PRD 4.K）：深色紧凑等宽。
 *
 * 设计基调（4.K 修订）：现代极简，借鉴 Termius——低饱和中性色系、
 * 高密度清晰层级、终端控件原生质感、深色可读性。
 */
import { useMemo } from 'react'
import { theme as antdTheme } from 'antd'
import type { ThemeConfig } from 'antd'

// 低饱和中性色板（Termius 风格灰阶：三级层级律）
const DESKTOP_DARK: Record<string, string> = {
  bg: '#1e1f24',          // 页面底
  panel: '#26272e',       // 面板/卡片
  float: '#2e3038',       // 悬浮/弹层
  text: '#d5d9e0',        // 正文（AA 对比，非纯白）
  textMuted: '#8b909a',
  border: '#3a3d46',
  primary: '#5b8bc4',     // 降饱和蓝（antd 默认 #1677ff → 低饱和）
  success: '#57a773',
  error: '#d07070',
}

export function useDesktopTheme() {
  return useMemo((): { isDesktop: boolean; themeConfig: ThemeConfig } => {
    return {
      isDesktop: true,
      themeConfig: {
        algorithm: antdTheme.darkAlgorithm,
        token: {
          colorPrimary: DESKTOP_DARK.primary,
          colorBgBase: DESKTOP_DARK.bg,
          colorBgContainer: DESKTOP_DARK.panel,
          colorBgElevated: DESKTOP_DARK.float,
          colorText: DESKTOP_DARK.text,
          colorTextSecondary: DESKTOP_DARK.textMuted,
          colorBorder: DESKTOP_DARK.border,
          colorBorderSecondary: DESKTOP_DARK.border,
          borderRadius: 4,
          // 紧凑密度
          controlHeight: 28,
          // v2.8：用户反馈整体偏小 → 13 → 14（proto.css 侧同批 +1px）
          fontSize: 14,
        },
        components: {
          Table: { cellPaddingBlockSM: 6, fontSize: 14 },   // 行高≈32
          Layout: { siderBg: DESKTOP_DARK.panel, headerBg: DESKTOP_DARK.panel, bodyBg: DESKTOP_DARK.bg },
          Menu: { darkItemBg: DESKTOP_DARK.panel, darkItemSelectedBg: DESKTOP_DARK.float },
        },
      },
    }
  }, [])
}

export { DESKTOP_DARK }
