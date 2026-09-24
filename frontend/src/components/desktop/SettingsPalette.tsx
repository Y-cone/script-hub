/**
 * V5-G 设置浮层（SPEC §2.9 v2.14 / 原型 §6.5 四态）。
 *
 * 交互：入口 = 顶栏齿轮（无快捷键）；关闭 = Esc / 遮罩 / 底部「取消」（丢弃未保存改动）。
 * 四态：①默认（绿点·无状态条·保存禁用）②有改动未保存（黄点·状态条）③已保存待重启（黄点·立即重启）
 * ④校验失败（红点·红字·描边·保存禁用）。校验防抖 300ms；不弹 Message、不覆盖已输入值。
 * 置灰唯一依据 = system/info 的 readonly（前端不猜环境变量）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import {
  getSystemInfo, putSettings, validateSettings,
} from '../../services/api'
import type { SettingsValidateResult, SystemInfo } from '../../services/api'

type FieldKey = 'data_dir' | 'scripts_root'
type DotState = 'ok' | 'warn' | 'err'

const DOT_COLOR: Record<DotState, string> = {
  ok: 'var(--ok)',
  warn: 'var(--warn)',
  err: 'var(--err)',
}

/**
 * v2.14 §2.9 映射（v2.15 收紧）：error→红；否则由调用方按 dirty/savedPending 定黄绿。
 * 「目录存在（空）」只作灰文案，不再判黄——黄点 = 有改动或已保存待重启。
 */
function fieldError(v: SettingsValidateResult['data_dir'] | SettingsValidateResult['scripts_root']): string | null {
  return v?.error ?? null
}

function statusLine(field: FieldKey, v: SettingsValidateResult[FieldKey], savedPending: boolean): { text: string; err: boolean } {
  if (savedPending) return { text: '已保存 · 待重启生效', err: false }
  if (!v) return { text: '', err: false }
  if (v.error) return { text: v.error, err: true }
  if (!v.exists) return { text: '目录不存在，保存时将创建', err: false }
  if (field === 'data_dir') {
    const size = (v as { size_mb?: number }).size_mb
    if (v.empty) return { text: '目录存在（空）', err: false }
    return { text: `目录存在 · 可写${size != null ? ` · 当前占用 ${size} MB` : ''}`, err: false }
  }
  const sc = (v as { script_count?: number }).script_count ?? 0
  const dc = (v as { dir_count?: number }).dir_count ?? 0
  if (v.empty) return { text: `目录存在 · 保存后脚本库为 0 个脚本`, err: false }
  return { text: `目录存在 · ${sc} 个脚本 / ${dc} 个目录`, err: false }
}

export default function SettingsPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [info, setInfo] = useState<SystemInfo | null>(null)
  const [drafts, setDrafts] = useState<Record<FieldKey, string>>({ data_dir: '', scripts_root: '' })
  const [editing, setEditing] = useState<Record<FieldKey, boolean>>({ data_dir: false, scripts_root: false })
  const [validate, setValidate] = useState<SettingsValidateResult>({})
  const [savedPending, setSavedPending] = useState<Record<FieldKey, boolean>>({ data_dir: false, scripts_root: false })
  const [dirty, setDirty] = useState<Record<FieldKey, boolean>>({ data_dir: false, scripts_root: false })
  const [saving, setSaving] = useState(false)
  const [migrate, setMigrate] = useState(false)
  const [warnings, setWarnings] = useState<string[]>([])
  const [restartError, setRestartError] = useState('')
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined)

  // 打开：拉取运行配置 + readonly；丢弃一切本地态（关闭重开 = 干净面板）
  useEffect(() => {
    if (!open) return
    getSystemInfo()
      .then((res) => {
        const d = res.data
        setInfo(d)
        setDrafts({ data_dir: d.data_dir || '', scripts_root: d.scripts_root || '' })
      })
      .catch(() => {})
    setValidate({})
    setEditing({ data_dir: false, scripts_root: false })
    setSavedPending({ data_dir: false, scripts_root: false })
    setDirty({ data_dir: false, scripts_root: false })
    setMigrate(false)
    setWarnings([])
    setRestartError('')
  }, [open])

  const anyDirty = dirty.data_dir || dirty.scripts_root
  const anySavedPending = savedPending.data_dir || savedPending.scripts_root
  const anyError = !!(validate.data_dir?.error || validate.scripts_root?.error)

  // 校验防抖 300ms：打开面板先对两字段各跑一次 baseline（状态行立刻有内容），之后只校验「有改动」的字段
  useEffect(() => {
    if (!open) return
    const run = (body: Record<string, string>) =>
      validateSettings(body).then((r) => setValidate((old) => ({ ...old, ...r.data }))).catch(() => {})
    if (!anyDirty) {
      // F2：baseline = 当前生效值（info 拉到后 drafts 已就位）
      if (drafts.data_dir || drafts.scripts_root) {
        run({
          ...(drafts.data_dir ? { data_dir: drafts.data_dir } : {}),
          ...(drafts.scripts_root ? { scripts_root: drafts.scripts_root } : {}),
        })
      }
      return
    }
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      const body: Record<string, string> = {}
      if (dirty.data_dir) body.data_dir = drafts.data_dir
      if (dirty.scripts_root) body.scripts_root = drafts.scripts_root
      run(body)
    }, 300)
    return () => clearTimeout(debounceRef.current)
  }, [drafts, dirty, open, anyDirty])

  const doSave = useCallback(async () => {
    if (!anyDirty || anyError || saving) return
    setSaving(true)
    try {
      const body: Record<string, unknown> = {}
      if (dirty.data_dir) body.data_dir = drafts.data_dir
      if (dirty.scripts_root) body.scripts_root = drafts.scripts_root
      if (dirty.data_dir && migrate) body.migrate = true
      const res = await putSettings(body as never)
      const pending = { ...savedPending }
      if (dirty.data_dir) pending.data_dir = true
      if (dirty.scripts_root) pending.scripts_root = true
      setSavedPending(pending)
      setDirty({ data_dir: false, scripts_root: false })
      // warnings 只进状态条区域灰字，不弹 Message
      setWarnings(res.data.warnings || [])
    } finally {
      setSaving(false)
    }
  }, [anyDirty, anyError, saving, dirty, drafts, savedPending])

  const browse = useCallback(async (field: FieldKey) => {
    const { open: dlgOpen } = await import('@tauri-apps/plugin-dialog')
    const path = await dlgOpen({ directory: true })
    if (typeof path === 'string' && path) {
      setDrafts((d) => ({ ...d, [field]: path }))
      setDirty((x) => ({ ...x, [field]: true }))
    }
  }, [])

  // Esc 关闭（遮罩点击在 onMouseDown）
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open || !info) return null

  const readonlyMap = info.readonly || { data_dir: false, scripts_root: false }
  const fields: { key: FieldKey; title: string; sub: string }[] = [
    { key: 'data_dir', title: '数据目录', sub: 'scripthub.db · 运行历史 / 定时任务' },
    { key: 'scripts_root', title: '脚本根目录', sub: '脚本库扫描范围' },
  ]

  const renderCard = (f: { key: FieldKey; title: string; sub: string }) => {
    const ro = readonlyMap[f.key]
    const isEditing = editing[f.key] && !ro
    const v = validate[f.key]
    const dot: DotState = fieldError(v) ? 'err'
      : dirty[f.key] || savedPending[f.key] ? 'warn'
      : 'ok'
    const line = statusLine(f.key, v, savedPending[f.key])
    return (
      <div className={`setcard${isEditing ? ' editing' : ''}`} key={f.key}>
        <div className="sc-h">
          <span className="dot" style={{ background: DOT_COLOR[dot] }} data-dot={dot} />
          <b>{f.title}</b>
          <span className="mono">{f.sub}</span>
          {ro && <span className="tag" title="由环境变量决定，不可在此修改">只读</span>}
          {!ro && !isEditing && (
            <span className="tag" style={{ cursor: 'pointer' }} data-testid={`edit-${f.key}`}
              onClick={() => setEditing((e) => ({ ...e, [f.key]: true }))}>更改</span>
          )}
        </div>
        {isEditing ? (
          <div className="sc-e">
            <input className="in" data-testid={`input-${f.key}`} value={drafts[f.key]} spellCheck={false}
              onChange={(e) => {
                const val = e.target.value
                setDrafts((d) => ({ ...d, [f.key]: val }))
                setDirty((x) => ({ ...x, [f.key]: val !== (f.key === 'data_dir' ? info.data_dir : info.scripts_root) }))
              }} />
            <span className="btn" style={{ cursor: 'pointer' }} onClick={() => browse(f.key)}>浏览…</span>
          </div>
        ) : (
          <div className="sc-v" style={ro ? { opacity: 0.6 } : undefined}>{drafts[f.key]}</div>
        )}
        <div className={`sc-m${line.err ? ' err' : ''}`} style={line.err ? { color: 'var(--err)' } : undefined}>
          {line.text}
        </div>
      </div>
    )
  }

  return (
    <div onMouseDown={onClose} data-testid="settings-mask" style={{
      position: 'fixed', inset: 0, zIndex: 2000, background: 'rgba(0,0,0,.45)',
      display: 'flex', justifyContent: 'center', alignItems: 'flex-start', paddingTop: 64,
    }}>
      <div className="modal settings" onMouseDown={(e) => e.stopPropagation()} style={{
        background: 'var(--sh-float, #2e3038)', border: '1px solid var(--sh-border, #3a3d46)',
        borderRadius: 6, boxShadow: '0 20px 60px rgba(0,0,0,.5)',
      }}>
        <div className="mh" style={{
          padding: '12px 16px', borderBottom: '1px solid var(--sh-border, #3a3d46)',
          fontSize: 13.5, fontWeight: 600,
        }}>设置</div>
        <div className="mb" style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column' }}>
          {/* 状态条：② 黄 ③ 黄+立即重启 ④ 红；① 默认态不渲染 */}
          {anyError && (
            <div className="sstrip err" data-testid="sstrip">
              <span className="dot" style={{ background: DOT_COLOR.err }} />
              <span>1 项校验未通过 · 保存不可用</span>
            </div>
          )}
          {anySavedPending && !anyDirty && !anyError && (
            <div className="sstrip warn" data-testid="sstrip">
              <span className="dot" style={{ background: DOT_COLOR.warn }} />
              <span>已保存 · 需重启应用生效</span>
              <span className="btn" style={{ marginLeft: 'auto', cursor: 'pointer' }} data-testid="restart-btn"
                onClick={() => {
                  setRestartError('')
                  invoke('restart_app').catch((e) => setRestartError(`重启失败：${e}`))
                }}>立即重启</span>
            </div>
          )}
          {restartError && (
            <div className="sstrip" data-testid="restart-error" style={{ color: 'var(--sh-muted, #8b909a)' }}>
              {restartError}
            </div>
          )}
          {warnings.length > 0 && (
            <div className="sstrip" data-testid="settings-warnings" style={{ color: 'var(--sh-muted, #8b909a)' }}>
              {warnings.join('；')}
            </div>
          )}
          {anyDirty && !anyError && (
            <div className="sstrip warn" data-testid="sstrip">
              <span className="dot" style={{ background: DOT_COLOR.warn }} />
              <span>1 项改动尚未保存</span>
            </div>
          )}
          {fields.map(renderCard)}
          <div className="setrow">
            <span className={`switch${migrate ? '' : ' off'}`} data-testid="migrate-switch" onClick={() => setMigrate((m) => !m)} />
            <span>切换数据目录时迁移现有数据</span>
            <span className="mono">复制当前库 · 原目录保留</span>
          </div>
        </div>
        <div className="mf" style={{
          padding: '12px 16px', borderTop: '1px solid var(--sh-border, #3a3d46)',
          display: 'flex', justifyContent: 'flex-end', gap: 8, alignItems: 'center',
        }}>
          <span style={{ margin: '0 auto 0 0', fontSize: 11.5, color: 'var(--sh-muted, #8b909a)' }}>
            {(readonlyMap.data_dir || readonlyMap.scripts_root) ? '部分字段由环境变量决定（只读）' : '环境变量存在时对应字段只读'}
          </span>
          <span className="btn" data-testid="cancel" style={{ cursor: 'pointer' }} onClick={onClose}>取消</span>
          <span className={`btn pri${!anyDirty || anyError ? ' off' : ''}`} data-testid="save"
            style={{ cursor: anyDirty && !anyError ? 'pointer' : 'default', background: anyDirty && !anyError ? 'var(--pri)' : undefined, color: anyDirty && !anyError ? '#fff' : undefined }}
            onClick={doSave}>保存</span>
        </div>
      </div>
    </div>
  )
}
