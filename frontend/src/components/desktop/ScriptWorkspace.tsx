/**
 * V5-G 桌面脚本工作区（PRD 4.K 设计基准 v2）：
 * 左 = 脚本头（名/标签）+ 输出流（内嵌 ScriptDetail 提供内容/编辑/参数/执行全部能力）；
 * 右 = 300px 参数栏（设备 + 执行/定时 + 近 5 次历史）。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Spin, message } from 'antd'
import { useParams, useNavigate } from 'react-router-dom'
import { useScriptStore } from '../../stores/scriptStore'
import { getRunHistory, getDevices, exportScript, getTags, updateScript } from '../../services/api'
import type { RunHistoryItem } from '../../services/api'
import { useDeviceContext } from '../../stores/deviceContext'
import { durationText } from '../../utils/format'
import { saveBinaryFile } from '../../services/desktop'
import Sel from './Sel'

// SPEC §2.4：Shell 列表按设备平台（前端常量，无接口）
const SHELL_BY_TYPE: Record<string, { value: string; label: string }[]> = {
  linux: [
    { value: '', label: '自动' },
    { value: 'bash', label: 'bash' },
    { value: 'sh', label: 'sh' },
    { value: 'python3', label: 'python3' },
  ],
  windows: [
    { value: '', label: '自动' },
    { value: 'powershell', label: 'PowerShell' },
    { value: 'pwsh', label: 'pwsh' },
    { value: 'cmd', label: 'CMD' },
    { value: 'bash', label: 'Git Bash' },
  ],
  mac: [
    { value: '', label: '自动' },
    { value: 'zsh', label: 'zsh' },
    { value: 'bash', label: 'bash' },
  ],
}
// 本机目标：按本地 OS 出候选（后端以 sys.platform 为准并兜底校验）
const LOCAL_SHELLS = typeof navigator !== 'undefined' && /Windows/i.test(navigator.userAgent)
  ? SHELL_BY_TYPE.windows
  : SHELL_BY_TYPE.linux

export default function ScriptWorkspace() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { currentScript, fetchScript, fetchContent } = useScriptStore()
  const { currentDeviceId, setCurrentDeviceId } = useDeviceContext()
  const [devices, setDevices] = useState<{ id: number; name: string; type?: string }[]>([])
  const [recent, setRecent] = useState<RunHistoryItem[]>([])
  const [outputTick, setOutputTick] = useState(0)
  const [shell, setShell] = useState('')
  // 批次 AY（用户拍板，取代 AX ②）：右栏「超时」= **该脚本超时的唯一编辑入口**，点「执行」时落库到脚本记录
  // （`updateScript(id, { timeout })`），故填写值跨会话保留；「脚本配置」tab 里的 timeout 输入框已撤销。
  // 进入脚本 / 切脚本时以 `currentScript.timeout`（DB 值）为初值。
  // 「没设置」的落地：清空框 → `Number('') || 0` = 0 → `executeScript` 的 `|| undefined` 省略该字段
  // → 后端 `run.py:113 timeout=request.timeout or script.timeout or 0`；而 0 也已按新口径落到脚本记录 = 不限，
  // 两条路一致。`type=number` 留空＝0＝不限，语义自洽，故不上 `number | ''` 那套额外判空。
  // 依赖 store 的 `currentScript?.id` 而非 URL id：对象被替换但脚本没变时不重跑，不打断用户手填的值。
  const [timeoutVal, setTimeoutVal] = useState(0)
  // eslint-disable-next-line react-hooks/exhaustive-deps -- 只认脚本身份：对象替换但 id 未变时不重跑，见上
  useEffect(() => { setTimeoutVal(currentScript?.timeout || 0) }, [currentScript?.id])
  // 脚本头标签芯片的色点（标签名 → 颜色，未知标签回落 --sh-dim；与列表页 tagColor 同写法）
  const [tags, setTags] = useState<{ name: string; color?: string }[]>([])
  const tagColor = (name: string) => tags.find((t) => t.name === name)?.color || 'var(--sh-dim)'
  useEffect(() => {
    const load = () => getTags().then((res) => setTags(res.data.items || [])).catch(() => {})
    load()
    // 管理浮层改名/改色后同步色点（不留旧色）
    window.addEventListener('scripthub:tags-changed', load)
    return () => window.removeEventListener('scripthub:tags-changed', load)
  }, [])

  // v2.8（用户）：本次执行的参数值改在右栏输入（原在输出 tab 上方的运行配置卡里，已移除）
  const paramDefs = useMemo(() => {
    try {
      const parsed = JSON.parse(currentScript?.parameters || '[]')
      return Array.isArray(parsed) ? (parsed as { name: string; type?: string; required?: boolean; default?: string; description?: string }[]) : []
    } catch { return [] }
  }, [currentScript?.parameters])
  const [paramVals, setParamVals] = useState<Record<string, string>>({})
  useEffect(() => {
    const vals: Record<string, string> = {}
    paramDefs.forEach((p) => { vals[p.name] = p.default ?? '' })
    setParamVals(vals)
  }, [paramDefs])

  useEffect(() => {
    getDevices().then((res) => setDevices(res.data || [])).catch(() => {})
  }, [id])

  // 近 5 次历史；执行后（输出回流事件）刷新
  useEffect(() => {
    if (!id) return
    getRunHistory({ script_id: Number(id), page: 1, page_size: 5 })
      .then((res) => setRecent(res.data.items || []))
      .catch(() => {})
  }, [id, outputTick])

  // 批次 BD：F5 刷新（useGlobalShortcuts 派发 scripthub:refresh）—— 本页可见数据 = 左栏脚本记录/预览内容
  // （store 的既有 fetch 方法，不另写取数）+ 右栏「近 5 次」（复用 :92 那路 outputTick）。
  // 不重挂页面：运行态/输出由内嵌 ScriptDetail 持有，重挂会丢掉「终止」能力。
  useEffect(() => {
    const onRefresh = () => {
      if (!id) return
      void fetchScript(Number(id))
      void fetchContent(Number(id))
      setOutputTick((t) => t + 1)
    }
    window.addEventListener('scripthub:refresh', onRefresh)
    return () => window.removeEventListener('scripthub:refresh', onRefresh)
  }, [id, fetchScript, fetchContent])

  useEffect(() => {
    const onUpd = () => setOutputTick((t) => t + 1)
    window.addEventListener('scripthub:output-updated', onUpd)
    return () => window.removeEventListener('scripthub:output-updated', onUpd)
  }, [])

  // 批次 AW ②：运行态（内嵌 ScriptDetail 持有）经 window 事件总线同步到此 —— 右栏「终止」的显隐与点击。
  // 同 scripthub:run 既有模式，不新建 store：状态唯一的拥有者仍是 ScriptDetail。
  const [running, setRunning] = useState(false)
  useEffect(() => {
    const onRunState = (e: Event) => setRunning(!!(e as CustomEvent<{ running: boolean }>).detail?.running)
    window.addEventListener('scripthub:run-state', onRunState)
    return () => window.removeEventListener('scripthub:run-state', onRunState)
  }, [])

  // SPEC §2.4：参数栏「导出」——复用既有 GET /scripts/{id}/export（零新接口）
  const handleExportZip = async () => {
    if (!id) return
    try {
      const res = await exportScript(Number(id))
      const bytes = new Uint8Array(await (res.data as Blob).arrayBuffer())
      const saved = await saveBinaryFile(`script_${id}.zip`, bytes)
      if (saved) message.success(`已导出：${saved}`)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '导出失败')
    }
  }

  // 批次 AY（用户拍板）：点「执行」时把右栏超时落库到脚本记录 —— 唯一编辑入口 + 执行时保存，值跨会话保留。
  // ① fire-and-forget（不 await）：执行是主目的，落库不能阻塞事件播发；失败静默（不弹 error 打断执行）。
  //    `.catch` 已挂 → 不会产生 unhandled rejection。
  // ② 只在「右栏所属脚本 == URL 里的脚本」时落：切脚本瞬间 URL id 已变、currentScript 仍是上一个，
  //    此窗口点执行会把上一个脚本的超时写到新脚本头上（右侧整栏此时展示的也是上一个脚本的值）。
  // ③ 0 照落：0 = 不限，是合法且有意义的脚本级设置。
  // 批次 AZ（用户真机验收③）：落库成功后必须把 store 的 `currentScript` 一起更新 ——
  // 此前只写 DB、store 里 timeout 仍是旧值，而上面的回填 effect 只认 `currentScript?.id`：
  // 「执行（落库 777）→ 返回脚本库 → 再进同一脚本」时 store 还是旧对象（timeout=0）→ 输入框显示 0，
  // 用户看到的就是「改了超时没保存下来」（DB 其实已是 777）。服务端响应即权威值，直接回填。
  const persistTimeout = () => {
    if (!id || currentScript?.id !== Number(id)) return
    void updateScript(Number(id), { timeout: timeoutVal })
      .then((res) => {
        if (res.data) useScriptStore.setState({ currentScript: res.data })
      })
      .catch(() => {})
  }

  // 批次 AZ（用户真机验收③）：右栏是「超时/Shell/脚本参数」的唯一持有者，故执行只有一个出口——
  // 「执行」按钮与 Ctrl+Enter 都走它（原先 Ctrl+Enter 由 useGlobalShortcuts 直接派发无 detail 的
  // `scripthub:run`：右栏改的超时既不落库也不下发，Shell/参数同样丢失）。ref 转发保持监听只挂一次。
  const runCurrentScript = () => {
    persistTimeout()
    window.dispatchEvent(new CustomEvent('scripthub:run', { detail: { timeout: timeoutVal, shell: shell || null, params: paramVals } }))
  }
  const runRef = useRef(runCurrentScript)
  runRef.current = runCurrentScript
  useEffect(() => {
    const onHotkey = () => runRef.current()
    window.addEventListener('scripthub:run-hotkey', onHotkey)
    return () => window.removeEventListener('scripthub:run-hotkey', onHotkey)
  }, [])

  const paramBar = (
    <div className="right">
      <h5>运行参数</h5>
      <div className="fld">
        <label>目标设备</label>
        <Sel width="100%" value={currentDeviceId ?? 0}
          onChange={(v) => setCurrentDeviceId(Number(v) === 0 ? null : Number(v))}
          options={[{ value: 0, label: '本机' }, ...devices.map((d) => ({ value: d.id, label: d.name }))]} />
      </div>
      <div className="fld">
        <label>Shell</label>
        <Sel width="100%" value={shell} onChange={setShell}
          title="自动（按脚本类型）= shell→bash / powershell→PowerShell / python→python3；手动指定则覆盖（后端按目标平台白名单校验）"
          options={(currentDeviceId
            ? (SHELL_BY_TYPE[devices.find((d) => d.id === currentDeviceId)?.type || ''] || LOCAL_SHELLS)
            : LOCAL_SHELLS
          ).map((o) => ({ value: o.value, label: o.label }))} />
      </div>
      <div className="fld">
        <label title="超时（秒），0=不限。这是该脚本超时的唯一来源：点「执行」时会保存到脚本记录（下次进来即此值）。">超时（秒）</label>
        <input className="sel" style={{ width: '100%' }} type="number" min={0} value={timeoutVal}
          onChange={(e) => setTimeoutVal(Number(e.target.value) || 0)} placeholder="0=不限" />
      </div>
      {paramDefs.length > 0 && <div className="mono" style={{ color: 'var(--muted)' }}>脚本参数</div>}
      {paramDefs.map((p) => (
        p.type === 'bool' ? (
          <div className="fld" key={p.name} style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}
            title={p.description || p.name}>
            <label style={{ flex: 1 }}>{p.name}</label>
            <span className={`switch${paramVals[p.name] === 'true' ? '' : ' off'}`} style={{ cursor: 'pointer' }}
              onClick={() => setParamVals((v) => ({ ...v, [p.name]: v[p.name] === 'true' ? 'false' : 'true' }))} />
          </div>
        ) : (
          <div className="fld" key={p.name}>
            <label title={p.description || p.name}>{p.name}{p.required ? ' *' : ''}</label>
            <input className="sel" style={{ width: '100%' }} value={paramVals[p.name] ?? ''}
              placeholder={p.default || p.description || ''}
              onChange={(e) => setParamVals((v) => ({ ...v, [p.name]: e.target.value }))} />
          </div>
        )
      ))}
      {/* 批次 AW ②（用户拍板）：两行 —— ①执行 [+ 终止] ②定时 导出；原型 .right .run（flex + gap:8 + .btn{flex:1}）
          原样沿用，单行两行同规则 → 每个按钮占半栏，两行间距 = 原 margin-top:4px。
          批次 AZ（用户真机验收①）：用户反馈「看不到终止按钮」→ 改为**常驻**，非运行态淡显（沿用既有
          `.btn.off`，proto.css:869，同设置面板「保存」/ 设备页「已连接」）+ cursor:default + 不响应点击，
          布局仍是两行各半栏（不再有「执行独占整行」的两种宽度）。 */}
      <div className="run">
        <span className="btn pri" style={{ cursor: 'pointer' }}
          onClick={runCurrentScript}>▷ 执行</span>
        <span className={`btn${running ? ' danger' : ' off'}`}
          style={{ cursor: running ? 'pointer' : 'default', pointerEvents: running ? undefined : 'none' }}
          onClick={() => window.dispatchEvent(new CustomEvent('scripthub:kill'))}>终止</span>
      </div>
      <div className="run">
        <span className="btn" style={{ cursor: 'pointer' }} onClick={() => navigate('/schedules')}>定时</span>
        <span className="btn" style={{ cursor: 'pointer' }} onClick={handleExportZip}>导出</span>
      </div>
      <h5 style={{ marginTop: 6 }}>近 5 次</h5>
      {recent.length === 0 && <span className="mono">暂无运行记录</span>}
      {recent.map((r) => (
        <div className="row" key={r.id}>
          <span className={r.status === 'success' ? 'ok' : r.status === 'running' ? 'warn' : 'err'}>
            {r.status === 'success' ? '成功' : r.status === 'running' ? '▸ 运行中' : r.status === 'timeout' ? '⏱ 超时' : r.status === 'killed' ? '■ 已终止' : '失败'}
            {r.exit_code != null && r.exit_code !== 0 ? ` (exit ${r.exit_code})` : ''}
          </span>
          <span className="mono">{durationText(r.duration)}</span>
        </div>
      ))}
    </div>
  )


  if (!currentScript) {
    // ponytail: 内嵌 ScriptDetail 必须无条件挂载（它自己 fetchScript），否则互相等待死锁。
    return (
      <div className="dsk-detail" style={{ flex: 1, minHeight: 0, display: 'grid', gridTemplateColumns: '1fr 300px', overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Spin style={{ margin: 40 }} />
        </div>
        <ScriptDetailEmbedded />
      </div>
    )
  }

  return (
    <div className="dsk-detail" style={{ flex: 1, minHeight: 0, display: 'grid', gridTemplateColumns: '1fr 300px', overflow: 'hidden' }}>
      {/* 左：脚本头 42px（位于左栏内顶端，不横贯双栏——用户拍板 + SPEC §2.2）+ 工作区 */}
      <div style={{ minHeight: 0, display: 'grid', gridTemplateRows: '42px 1fr', overflow: 'hidden' }}>
      <div className="dh">
        <span className="btn" style={{ padding: '2px 8px', color: 'var(--muted)', cursor: 'pointer' }} onClick={() => navigate('/')}>←</span>
        <b>{currentScript.name}</b>
        <span className="tag">{currentScript.category}</span>
        {(currentScript.tags || []).slice(0, 4).map((t) => (
          <span className="tag chip" key={t} data-testid="ws-tag-chip">
            <span className="dotc" data-testid="ws-tag-dot" style={{ background: tagColor(t) }} />{t}
          </span>
        ))}
        {(currentScript.tags || []).length > 4 && <span className="tag">+{currentScript.tags.length - 4}</span>}
        {currentScript.dangerous && <span className="tag danger">⚠ 高危</span>}
        <span className="mono" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>
          {currentScript.relative_path}
        </span>
      </div>
        {/* 左下：内嵌 ScriptDetail（内容/编辑/参数/输出全部能力） */}
        <div style={{ minHeight: 0, overflow: 'auto' }}>
          <ScriptDetailEmbedded />
        </div>
      </div>
      {/* 右：参数栏（通高） */}
      <div style={{ height: '100%', minHeight: 0, overflow: 'auto' }}>{paramBar}</div>
    </div>
  )
}

/** ScriptDetail 嵌入渲染（动态 import 懒加载） */
function ScriptDetailEmbedded() {
  const [Comp, setComp] = useState<React.ComponentType | null>(null)
  useEffect(() => {
    import('../../pages/ScriptDetail').then((m) => setComp(() => m.default))
  }, [])
  if (!Comp) return <Spin style={{ margin: 40 }} />
  return <Comp />
}
