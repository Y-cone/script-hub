import { useEffect, useRef } from 'react'
import hljs from 'highlight.js/lib/core'
import python from 'highlight.js/lib/languages/python'
import bash from 'highlight.js/lib/languages/bash'
import dos from 'highlight.js/lib/languages/dos'
import powershell from 'highlight.js/lib/languages/powershell'
import 'highlight.js/styles/github-dark.css'

hljs.registerLanguage('python', python)
hljs.registerLanguage('shell', bash)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('bat', dos)
hljs.registerLanguage('powershell', powershell)

const LANG_MAP: Record<string, string> = {
  python: 'python',
  shell: 'bash',
  bat: 'dos',
  powershell: 'powershell',
}

export default function CodeViewer({ code, language }: { code: string; language: string }) {
  const ref = useRef<HTMLElement>(null)

  useEffect(() => {
    if (ref.current) {
      ref.current.removeAttribute('data-highlighted')
      ref.current.textContent = code
      hljs.highlightElement(ref.current)
    }
  }, [code, language])

  return (
    <pre style={{
      // V5-G（SPEC §0 令牌）：代码区用 --sh-term 独立质感层；圆角仅 4/6px；等宽字体走 --sh-mono
      // 外层 .dcard 是唯一的框，pre 只保留 --sh-term 底色（去 border/radius/padding，避免盒中盒）
      background: 'var(--sh-term, #191a1f)',
      border: 'none',
      borderRadius: 0,
      padding: '10px 12px',
      // 长行自动换行（不横向滚动），竖向滚动保留
      whiteSpace: 'pre-wrap',
      wordBreak: 'break-word',
      overflowX: 'hidden',
      overflowY: 'auto',
      maxHeight: 500,
      fontFamily: 'var(--sh-mono, ui-monospace, monospace)',
      fontSize: 12.5,
      lineHeight: 1.55,
    }}>
      {/* 行内 style 压过 highlight.js 主题的 .hljs{padding:1em;background:#0d1117}——
          否则 pre 的 16px 里再套一层 1em，肉眼就是「多了一层 padding」（用户反馈） */}
      <code ref={ref} className={LANG_MAP[language] || language}
        style={{ padding: 0, background: 'transparent', color: '#a8b3c2' }}>
        {code}
      </code>
    </pre>
  )
}
