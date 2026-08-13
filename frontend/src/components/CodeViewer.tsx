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
      background: '#1e1e1e',
      borderRadius: 8,
      padding: 16,
      overflow: 'auto',
      maxHeight: 500,
      fontSize: 13,
      lineHeight: 1.5,
    }}>
      <code ref={ref} className={LANG_MAP[language] || language}>
        {code}
      </code>
    </pre>
  )
}
