/**
 * 自绘下拉（I-2）：原生 <select> 的弹出列表由系统/GTK 绘制，CSS 管不到（`option{}` 在多数平台无效）
 * → 深色主题里弹出的列表永远「简陋」。这里用轻量自绘件替换，三处页面共用同一个。
 *
 * 关闭态几何与 .sel 逐项相同（实测）：高 26 / r4 / 1px --border / 底 --panel2 / 13px --text / 右侧自绘 ▾
 * 打开态走原型浮层规范：底 --float / 1px --border / r6 / shadow / 选项高 26 / 内距 0 10px /
 *   hover 底 --hover / 选中项左侧 2px --pri 竖条 + 文字 --pri
 *
 * 用法（贴近原生）：单选 <Sel value onChange options/>；多选 <Sel values onToggle options/>（多选不自动收起）。
 * ponytail: 浮层是 absolute（跟随包裹器），滚动容器内过长会被裁 —— 现有三处调用都放得下；
 *   真遇到裁切再改 portal，别提前上。
 */
import { useEffect, useRef, useState } from 'react'

export type SelOption = { value: string | number; label: string }

type Props = {
  options: SelOption[]
  /** 单选：当前值（与 onChange 配对） */
  value?: string | number
  onChange?: (v: string) => void
  /** 多选：当前值集合（给了 values 即多选，与 onToggle 配对） */
  values?: (string | number)[]
  onToggle?: (v: string) => void
  width?: number | string
  placeholder?: string
  title?: string
  testid?: string
}

export default function Sel({ options, value, onChange, values, onToggle, width, placeholder = '', title, testid }: Props) {
  const multi = values !== undefined
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const rootRef = useRef<HTMLDivElement | null>(null)

  const cur = (multi ? (values as (string | number)[]) : [value ?? '']).map(String)
  const isOn = (v: string | number) => cur.includes(String(v))
  const label = multi
    ? placeholder + (cur.length ? ` (${cur.length})` : '')
    : options.find((o) => isOn(o.value))?.label ?? placeholder

  // 点击外部关闭
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => { if (!rootRef.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const toggle = () => {
    if (!open) setActive(Math.max(0, options.findIndex((o) => isOn(o.value))))
    setOpen((o) => !o)
  }

  const pick = (o: SelOption) => {
    if (multi) { onToggle?.(String(o.value)); return }
    onChange?.(String(o.value))
    setOpen(false)
    rootRef.current?.focus()   // 收在根上，键盘可继续操作
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { if (open) { e.stopPropagation(); setOpen(false) } return }
    if (e.key === 'Tab') { setOpen(false); return }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      if (!open) { setOpen(true); setActive(Math.max(0, options.findIndex((o) => isOn(o.value)))); return }
      setActive((a) => Math.min(options.length - 1, Math.max(0, a + (e.key === 'ArrowDown' ? 1 : -1))))
      return
    }
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      if (!open) { setOpen(true); return }
      const o = options[active]
      if (o) pick(o)
    }
  }

  return (
    <div className="selx" ref={rootRef} style={width === undefined ? undefined : { width }} data-testid={testid}>
      <div
        className={`selx-btn${open ? ' on' : ''}`}
        tabIndex={0}
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        title={title ?? (label || undefined)}
        onClick={toggle}
        onKeyDown={onKeyDown}
      >
        <span className="tx">{label}</span>
        <span className="ca">▾</span>
      </div>
      {open && (
        <div className="selx-pop" role="listbox">
          {options.length === 0 && <div className="selx-opt empty">无</div>}
          {options.map((o, i) => (
            <div
              key={String(o.value)}
              className={`selx-opt${isOn(o.value) ? ' on' : ''}${i === active ? ' act' : ''}`}
              role="option"
              aria-selected={isOn(o.value)}
              title={o.label}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pick(o)}
            >
              {o.label}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
