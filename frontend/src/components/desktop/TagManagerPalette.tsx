/**
 * V5-G 标签管理浮层（SPEC §2.3 v2.16 / 原型 §6.7）。宽 560。
 *
 * 行 = 9px 色点 + 名称 + 「N 个脚本」 + 5 档预设色 + 「改名 / 删除」；底部 = 「新标签名称…」+ 色板 + 「+ 添加」。
 * 改名 / 改色 / 新增 / 删除**即时生效**（调 API 后刷新，无「保存」按钮，底部只有「关闭」）。
 * 删除 = 从所有脚本摘除（脚本与运行历史不受影响）+ 二次确认显示影响脚本数。
 * 重名 → 后端 409 → 输入行下红字（不弹 Message）。
 * 关闭三条通路：Esc / 遮罩 / 底部「关闭」；与搜索面板 / 帮助面板 / 设置面板互斥。
 * 入口：脚本库筛选行「管理标签…」+ 标签下拉内的「管理标签…」（scripthub:tags-manage）——同一浮层。
 */
import { useCallback, useEffect, useState } from 'react'
import { Modal } from 'antd'
import { getTags, getScripts, createTag, updateTag, deleteTag } from '../../services/api'
import type { TagItem } from '../../services/api'

/** 5 档预设色（与主题令牌同源；不做自由取色——保证任意底色上的可读性） */
const PRESETS: { hex: string; varName: string }[] = [
  { hex: '#5b8bc4', varName: '--sh-pri' },
  { hex: '#57a773', varName: '--sh-ok' },
  { hex: '#c9a54e', varName: '--sh-warn' },
  { hex: '#d07070', varName: '--sh-err' },
  { hex: '#8b909a', varName: '--sh-muted' },
]
const DEFAULT_COLOR = PRESETS[0].hex

/** 标签变更广播：脚本库列表 / 脚本配置 tab 基本信息的芯片据此刷新（无本地缓存残留） */
export const emitTagsChanged = () => window.dispatchEvent(new CustomEvent('scripthub:tags-changed'))

function errText(e: unknown): string {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return detail || '操作失败'
}

export default function TagManagerPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [tags, setTags] = useState<TagItem[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [name, setName] = useState('')
  const [color, setColor] = useState(DEFAULT_COLOR)
  const [err, setErr] = useState('')
  const [editing, setEditing] = useState<number | null>(null)
  const [editName, setEditName] = useState('')
  const [rowErr, setRowErr] = useState<{ id: number; text: string } | null>(null)

  const load = useCallback(() => {
    getTags().then((res) => setTags(res.data.items || [])).catch(() => {})
    // ponytail: 影响脚本数按前 100 个脚本统计（后端 page_size 上限 100）；超 100 时计数偏小
    getScripts({ page: 1, page_size: 100 })
      .then((res) => {
        const c: Record<string, number> = {}
        for (const s of res.data.items || []) for (const t of s.tags || []) c[t] = (c[t] || 0) + 1
        setCounts(c)
      })
      .catch(() => {})
  }, [])

  // 打开：拉取标签 + 影响脚本数；丢弃本地态（关闭重开 = 干净面板）
  useEffect(() => {
    if (!open) return
    load()
    setName('')
    setColor(DEFAULT_COLOR)
    setErr('')
    setEditing(null)
    setRowErr(null)
  }, [open, load])

  // 关闭通路 ①：Esc
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  // 互斥：Ctrl+K / Ctrl+/ 打开搜索面板 / 帮助面板时自身关闭（设置面板入口在顶栏，被本遮罩挡住）
  useEffect(() => {
    if (!open) return
    const close = () => onClose()
    window.addEventListener('scripthub:palette', close)
    window.addEventListener('scripthub:kbd', close)
    return () => {
      window.removeEventListener('scripthub:palette', close)
      window.removeEventListener('scripthub:kbd', close)
    }
  }, [open, onClose])

  if (!open) return null

  const after = () => { load(); emitTagsChanged() }

  const doAdd = async () => {
    const n = name.trim()
    if (!n) { setErr('请输入标签名'); return }
    try {
      await createTag({ name: n, color })
      setName('')
      setErr('')
      after()
    } catch (e) {
      setErr(errText(e))   // 重名 → 后端 409 → 此处红字，不弹 Message
    }
  }

  const startRename = (t: TagItem) => { setEditing(t.id); setEditName(t.name); setRowErr(null) }

  const commitRename = async (t: TagItem) => {
    const n = editName.trim()
    if (!n || n === t.name) { setEditing(null); return }
    try {
      await updateTag(t.id, { name: n })
      setEditing(null)
      setRowErr(null)
      after()
    } catch (e) { setRowErr({ id: t.id, text: errText(e) }) }
  }

  const commitColor = async (t: TagItem, hex: string) => {
    if (t.color?.toLowerCase() === hex.toLowerCase()) return
    try {
      await updateTag(t.id, { color: hex })
      setRowErr(null)
      after()
    } catch (e) { setRowErr({ id: t.id, text: errText(e) }) }
  }

  const askDelete = (t: TagItem) => {
    const n = counts[t.name] || 0
    Modal.confirm({
      title: `删除标签「${t.name}」？`,
      content: `将从 ${n} 个脚本上摘除该标签（脚本与运行历史均不受影响）`,
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => { await deleteTag(t.id); after() },
    })
  }

  const swatches = (current: string, onPick: (hex: string) => void) => (
    <div className="swatches">
      {PRESETS.map((p) => (
        <span
          key={p.hex}
          className={`swatch${current?.toLowerCase() === p.hex ? ' on' : ''}`}
          style={{ background: `var(${p.varName})` }}
          title={p.varName}
          data-swatch={p.hex}
          onClick={() => onPick(p.hex)}
        />
      ))}
    </div>
  )

  return (
    // 关闭通路 ②：遮罩
    <div onMouseDown={onClose} data-testid="tag-mask" style={{
      position: 'fixed', inset: 0, zIndex: 2000, background: 'rgba(0,0,0,.45)',
      display: 'flex', justifyContent: 'center', alignItems: 'flex-start', paddingTop: 64,
    }}>
      <div className="tagpanel" data-testid="tagpanel" onMouseDown={(e) => e.stopPropagation()}>
        <div className="ph">
          <b>标签管理</b>
          <span className="mono">{tags.length} 个标签</span>
          <span className="px" title="关闭" data-testid="tag-close-x" onClick={onClose}>✕</span>
        </div>

        {tags.map((t) => (
          <div className="trow" key={t.id}>
            <span className="dotc" style={{ width: 9, height: 9, background: t.color || 'var(--dim)' }} />
            {editing === t.id ? (
              <input
                className="nm in"
                autoFocus
                value={editName}
                data-testid={`tag-rename-input-${t.id}`}
                onChange={(e) => setEditName(e.target.value)}
                onBlur={() => commitRename(t)}
                onKeyDown={(e) => { if (e.key === 'Enter') commitRename(t) }}
              />
            ) : (
              <span className="nm" data-testid={`tag-name-${t.id}`}>{t.name}</span>
            )}
            <span className="use">{counts[t.name] || 0} 个脚本</span>
            {swatches(t.color, (hex) => commitColor(t, hex))}
            <span className="tag" style={{ cursor: 'pointer' }} data-testid={`tag-rename-${t.id}`} onClick={() => startRename(t)}>改名</span>
            {/* v2.21（P2）：删除芯片改走 CSS 类 .tag.danger（proto.css：文字 --err + 描边 #5a3a3a），
                不再 JSX 内联上色 —— 单一来源，且与「⚠ 高危」/调度页删除芯片同一条规则。 */}
            <span className="tag danger" style={{ cursor: 'pointer' }}
              data-testid={`tag-delete-${t.id}`} onClick={() => askDelete(t)}>删除</span>
            {rowErr?.id === t.id && <span className="tagerr">{rowErr.text}</span>}
          </div>
        ))}
        {tags.length === 0 && <div className="trow"><span className="mono">暂无标签</span></div>}

        <div className="addrow">
          <input
            className={`tinp${name ? '' : ' ph'}`}
            placeholder="新标签名称…"
            value={name}
            data-testid="tag-new-name"
            onChange={(e) => { setName(e.target.value); setErr('') }}
            onKeyDown={(e) => { if (e.key === 'Enter') doAdd() }}
          />
          {swatches(color, setColor)}
          <span className="btn pri" style={{ cursor: 'pointer' }} data-testid="tag-add" onClick={doAdd}>+ 添加</span>
        </div>
        {err && <div className="tagerr" data-testid="tag-new-err">{err}</div>}

        {/* 关闭通路 ③：底部「关闭」（无保存按钮） */}
        <div style={{
          marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--border)',
          display: 'flex', justifyContent: 'flex-end',
        }}>
          <span className="btn" style={{ cursor: 'pointer' }} data-testid="tag-close" onClick={onClose}>关闭</span>
        </div>
      </div>
    </div>
  )
}
