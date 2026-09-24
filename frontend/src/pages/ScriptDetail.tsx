import { useEffect, useState, useCallback, useRef, useMemo, type ReactNode } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Button, Spin, Table, Modal, Input, Select, Switch, Space, Popconfirm, message, Tag } from 'antd'
import { useScriptStore } from '../stores/scriptStore'
import type { ParamDef } from '../services/api'
import { runScript, killRun, updateScript, moveScript, getTags, setScriptTags, saveScriptContent, envCheckScript, getScriptDeps, getScriptDepCandidates, getSchedules } from '../services/api'
import type { TagItem, DepItem, ScheduleItem } from '../services/api'
import CodeViewer from '../components/CodeViewer'
import Terminal from '../components/Terminal'
import TagPicker from '../components/TagPicker'
import TagManagerPalette from '../components/desktop/TagManagerPalette'
import DirSelect from '../components/DirSelect'
import { useDeviceContext } from '../stores/deviceContext'
import { getWsBase } from '../config'

const TYPE_LABELS: Record<string, string> = {
  string: 'string',
  int: 'int',
  float: 'float',
  bool: 'bool',
}

/** V5-G（SPEC §2.3）：桌面形态五 tab，**唯一一份键序** —— Ctrl+1..5 的映射与 tab 条都读它。
 *  此前是两处各写一遍（键盘 handler 一份键名数组、tab 条一份 [键,标签] 数组），
 *  加第 6 个 tab 时 Ctrl 键序号会静默错位。 */
const TABS = [
  ['output', '输出'],
  ['preview', '脚本预览'],
  ['deps', '依赖检测'],
  ['schedule', '定时配置'],
  ['config', '脚本配置'],
] as const

function shiftToPrefix(category: string): string {
  switch (category) {
    case 'python': return 'python'
    case 'shell': return 'bash'
    case 'bat': return 'cmd'
    case 'powershell': return 'powershell'
    default: return ''
  }
}

/** 批次 AW ③：「保存配置」dirty 判据的归一化键 —— 恰好是 saveRunConfig 落库的 4 个字段，
 *  唯一一份口径（初值快照与现值都走它，字段增减只改这里）。
 *  批次 AY：timeout **已移出本键与 saveRunConfig 载荷** —— 超时现行口径是「右栏唯一入口 + 点执行时落库」，
 *  若仍由本段回写，会把刚存的超时值静默回滚成旧值。
 *  批次 BC：`timeout` state 与其加载回填一并删除 —— 执行请求的 timeout 只来自事件 detail（见 executeScript），
 *  而 `scripthub:run` 的唯一派发点（ScriptWorkspace）每次都带 detail.timeout，故 state 是死路径。 */
function cfgKey(workingDir: string, envVars: string, envRequests: string, dependencies: string[]) {
  return JSON.stringify([workingDir, envVars, envRequests, dependencies])
}

function buildCommand(scriptName: string, category: string, params: ParamDef[]): string {
  const prefix = shiftToPrefix(category)
  const args = params
    .map((p) => (p.type === 'bool' ? p.name : `${p.name} ${p.default || '<value>'}`))
    .join(' ')
  return `${prefix} ${scriptName} ${args}`.trim()
}

// 行尾标记（只读）· 单次扫描，不在 render 里对全文 split/includes（脚本可能几百 KB）
// 事实优先：混合行尾（既有 CRLF 又有裸 LF）如实显示，不按第一行/有无 \r 猜
function detectEol(s: string | null): string | null {
  if (!s) return null
  let crlf = 0
  let lf = 0
  for (let i = 0; i < s.length; i++) {
    if (s.charCodeAt(i) === 10) {
      if (i > 0 && s.charCodeAt(i - 1) === 13) crlf++
      else lf++
    }
  }
  if (crlf && lf) return `CRLF/LF 混合(${crlf}/${lf})`
  if (crlf) return 'CRLF'
  if (lf) return 'LF'
  return null // 无换行符（单行脚本）：无可标记的行尾
}

export default function ScriptDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  // V5-G（SPEC §2.3）：桌面形态五 tab（Ctrl+1..5 切换）；Web 形态平铺
  const [activeTab, setActiveTab] = useState('output')
  const { currentScript, scriptContent, scriptLanguage, fetchScript, fetchContent, parseParams, updateParams } = useScriptStore()

  const [params, setParams] = useState<ParamDef[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const [editingIndex, setEditingIndex] = useState<number | null>(null)
  const [form, setForm] = useState<ParamDef>({
    name: '',
    type: 'string',
    default: null,
    required: false,
    description: null,
    choices: null,
  })

  // 运行配置状态（从脚本DB记录加载）
  const [workingDir, setWorkingDir] = useState('')
  const [envVars, setEnvVars] = useState('')
  const [envRequests, setEnvRequests] = useState('')
  const [isRunning, setIsRunning] = useState(false)
  const [runOutput, setRunOutput] = useState('')
  const [runStatus, setRunStatus] = useState('')
  const [currentRunId, setCurrentRunId] = useState<number | null>(null)
  // 标签
  const [allTags, setAllTags] = useState<TagItem[]>([])
  const [scriptTagIds, setScriptTagIds] = useState<number[]>([])
  // v2.16 §0.5：三段折叠默认态（基本信息折 / 参数配置展 / 运行环境折）
  const [foldOpen, setFoldOpen] = useState({ basic: false, params: true, env: false })
  // v2.16 §6.7：标签管理浮层（入口 = 基本信息「管理」链接）；tagsTick 让 TagPicker 重新取标签，避免旧列表
  const [tagPanelOpen, setTagPanelOpen] = useState(false)
  const [tagsTick, setTagsTick] = useState(0)
  // 在线编辑
  const [editMode, setEditMode] = useState(false)
  const [editContent, setEditContent] = useState('')
  // 只读行尾标记：始终标**磁盘原文**的行尾（scriptContent）。编辑态不能看 editContent ——
  // 那是 <textarea> 规范化后的 LF，与保存后落盘的行尾不一致（批次 AJ：保存按磁盘原行尾还原）。
  const eolLabel = useMemo(() => detectEol(scriptContent), [scriptContent])
  const [saving, setSaving] = useState(false)
  // 依赖分析
  const [schedLoading, setSchedLoading] = useState(false)
  const [scriptSched, setScriptSched] = useState<ScheduleItem[]>([])
  const [deps, setDeps] = useState<DepItem[]>([])
  const [depsLoading, setDepsLoading] = useState(false)
  // 远程执行设备（由右上角"当前设备"全局上下文决定）
  const { currentDeviceId } = useDeviceContext()
  // 依赖文件清单（远程执行随传）
  const [dependencies, setDependencies] = useState<string[]>([])
  // 批次 AW ③：「运行环境与随传文件」段的初值快照（= 加载时 5 个字段的归一化键）。
  // 组件内 state，不引入新 store/全局态；null = 尚未从 DB 初始化 → 不显示保存按钮。
  const [cfgBase, setCfgBase] = useState<string | null>(null)
  const [depCandidates, setDepCandidates] = useState<string[]>([])

  useEffect(() => {
    if (id) {
      fetchScript(Number(id))
      fetchContent(Number(id))
    }
  }, [id, fetchScript, fetchContent])

  useEffect(() => {
    getTags().then((res) => setAllTags(res.data.items)).catch(() => {})
  }, [])

  // v2.16 §6.7 联动：管理浮层里改名/改色/删除后 → 本页脚本标签与标签列表同步刷新（不留本地缓存）
  useEffect(() => {
    if (!id) return
    const onTags = () => {
      fetchScript(Number(id))
      getTags().then((res) => setAllTags(res.data.items)).catch(() => {})
      setTagsTick((t) => t + 1)
    }
    window.addEventListener('scripthub:tags-changed', onTags)
    return () => window.removeEventListener('scripthub:tags-changed', onTags)
  }, [id, fetchScript])

  // G2-5：标签下拉内的「管理标签…」→ 打开同一标签管理浮层（浮层实例在下方按桌面形态挂载）
  useEffect(() => {
    const onManage = () => setTagPanelOpen(true)
    window.addEventListener('scripthub:tags-manage', onManage)
    return () => window.removeEventListener('scripthub:tags-manage', onManage)
  }, [])

  // V5-F F2：Ctrl+Enter 执行（全局快捷键派发事件）；ref 持有最新 handleRun
  const runRef = useRef<() => void>(() => {})
  // 批次 AW ②：参数栏「终止」经 scripthub:kill 回调到此（handleKill 定义在下方 → ref 转发）
  const killRef = useRef<() => void>(() => {})
  const extTimeoutRef = useRef<number | null>(null)
  const extShellRef = useRef<string | null>(null)
  // v2.8（用户）：本次执行的参数值改由右栏「运行参数」输入（原在输出 tab 上方的运行配置卡里，已移除）
  // → 随 `scripthub:run` 的 detail 下发，此处只做 ref 承接（页内输入控件已随 Web 形态删除，批次 BC）
  const extParamsRef = useRef<Record<string, string> | null>(null)
  useEffect(() => {
    const onRun = (e: Event) => {
      // 右栏「超时」随执行事件下发（该栏是超时的唯一来源；批次 BC 后本组件不再持有 timeout state）
      const d = (e as CustomEvent<{ timeout?: number; shell?: string | null; params?: Record<string, string> }>).detail
      if (typeof d?.timeout === 'number') extTimeoutRef.current = d.timeout
      extShellRef.current = d?.shell ?? null
      extParamsRef.current = d?.params ?? null
      runRef.current()
    }
    window.addEventListener('scripthub:run', onRun)
    return () => window.removeEventListener('scripthub:run', onRun)
  }, [])

  // 批次 AW ②：桌面形态「终止」在右栏（ScriptWorkspace）——本组件仍是运行态唯一持有者，
  // ① 把运行态下发（按钮显隐）② 接收右栏点击（执行终止）。走既有 window 事件总线，不新增 store。
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('scripthub:run-state', { detail: { running: isRunning } }))
  }, [isRunning])
  useEffect(() => {
    const onKill = () => killRef.current()
    window.addEventListener('scripthub:kill', onKill)
    return () => window.removeEventListener('scripthub:kill', onKill)
  }, [])

  // V5-G（SPEC §2.3）：桌面 tab 按需取数——依赖检测 tab → deps；定时配置 tab → 本脚本任务
  useEffect(() => {
    if (!id) return
    if (activeTab === 'deps' && deps.length === 0) handleDeps()
    if (activeTab === 'schedule') {
      setSchedLoading(true)
      getSchedules()
        .then((res) => setScriptSched((res.data.items || []).filter((s) => s.script_id === Number(id))))
        .catch(() => {})
        .finally(() => setSchedLoading(false))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, id])

  // V5-G（SPEC §3.3）：Ctrl+1..5 切工作区 tab
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.ctrlKey || e.altKey || e.shiftKey) return
      const n = Number(e.key)
      if (n >= 1 && n <= 5) {
        e.preventDefault()
        setActiveTab(TABS[n - 1][0])
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // 加载依赖文件候选列表（脚本根目录所有文件）
  useEffect(() => {
    if (id) getScriptDepCandidates(Number(id)).then((res) => setDepCandidates(res.data.files || [])).catch(() => {})
  }, [id])

  // 批次 AX ④（用户拍板）：原来是一个 `[currentScript, allTags]` 大 effect —— 只要 `currentScript` 对象被替换
  // （保存脚本内容 / 点危险开关 / 改标签 / fetchScript）或 `allTags` 变化（标签浮层改名改色）就把下面 5 个
  // 配置字段重置回 DB 值 → 用户在「脚本配置」段里尚未保存的编辑被静默丢掉。修法 = 拆三段 + 收窄依赖。

  // ① 运行配置（5 字段 + 初值快照）：只随**脚本身份**加载。依赖取 store 的 `currentScript?.id`，不是 useParams 的 id ——
  //    切 /scripts/2 → /scripts/3 时 URL 先变、store 里还是 2 的数据，此刻跑会快照到**上一个脚本**，且新数据到位后不再重跑；
  //    取 store 的 id 则「新脚本数据到位（id 变化）」才跑，读到的必然是新脚本 → 切脚本照常重置。
  //    对象被替换但 id 没变时**不重跑** → 用户正在编辑的字段得以保留（这正是本批次要的行为）。
  useEffect(() => {
    if (currentScript) {
      const wd0 = currentScript.working_dir || ''
      const ev0 = currentScript.env_vars || ''
      const er0 = currentScript.env_requests || ''
      let dep0: string[] = []
      try {
        dep0 = currentScript.dependencies ? JSON.parse(currentScript.dependencies) : []
      } catch {
        dep0 = []
      }
      setWorkingDir(wd0)
      setEnvVars(ev0)
      setEnvRequests(er0)
      setDependencies(dep0)
      // 批次 AW ③：初值快照与上面字段**同点**写入（同一批值，不留第二个真相）
      setCfgBase(cfgKey(wd0, ev0, er0, dep0))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只认脚本身份，见上方注释
  }, [currentScript?.id])

  // ② 参数定义变化 → 重解析。独立：参数变化确实要重跑，但不该连带重置上面 5 个字段
  useEffect(() => {
    try {
      setParams(JSON.parse(currentScript?.parameters || '[]'))
    } catch {
      setParams([])
    }
  }, [currentScript?.parameters])

  // ③ 标签初始化（名称 → id）。独立：`allTags` 变化确实要重跑，但不该连带重置上面 5 个字段
  useEffect(() => {
    if (currentScript?.tags?.length && allTags.length) {
      const idList = currentScript.tags
        .map((name) => allTags.find((t) => t.name === name)?.id)
        .filter((x): x is number => x != null)
      setScriptTagIds(idList)
    } else {
      setScriptTagIds([])
    }
  }, [currentScript?.id, currentScript?.tags, allTags])

  // 保存脚本内容（二次确认后）
  const handleSaveContent = useCallback(async () => {
    if (!id) return
    Modal.confirm({
      title: '保存脚本内容',
      content: '保存后将覆盖磁盘文件并重新扫描，确认？',
      okText: '保存',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        setSaving(true)
        try {
          await saveScriptContent(Number(id), editContent)
          // 重新拉取内容（含重扫后的最新状态）
          useScriptStore.getState().fetchContent(Number(id)).then(() => {
            useScriptStore.getState().fetchScript(Number(id))
          })
          // 内容已变，自动重新解析参数
          try {
            const parsed = await useScriptStore.getState().parseParams(Number(id))
            setParams(parsed)
          } catch {
            // 解析失败不阻断保存，保留旧参数
          }
          setEditMode(false)
          message.success('已保存并重新扫描')
        } catch (e: any) {
          message.error(e?.response?.data?.detail || '保存失败')
        } finally {
          setSaving(false)
        }
      },
    })
  }, [id, editContent])

  const handleParse = async () => {
    if (!id) return
    try {
      const parsed = await parseParams(Number(id))
      setParams(parsed)
      message.success(`解析到 ${parsed.length} 个参数`)
    } catch {
      message.error('解析失败')
    }
  }

  const persist = useCallback(async (newParams: ParamDef[]) => {
    if (!id) return
    await updateParams(Number(id), newParams)
  }, [id, updateParams])

  const openAdd = () => {
    setEditingIndex(null)
    setForm({ name: '', type: 'string', default: null, required: false, description: null, choices: null })
    setModalOpen(true)
  }

  const openEdit = (idx: number) => {
    setEditingIndex(idx)
    setForm({ ...params[idx] })
    setModalOpen(true)
  }

  const handleDelete = async (idx: number) => {
    const next = params.filter((_, i) => i !== idx)
    setParams(next)
    await persist(next)
  }

  const handleSave = async () => {
    if (!form.name) {
      message.warning('参数名不能为空')
      return
    }
    let next: ParamDef[]
    if (editingIndex !== null) {
      next = [...params]
      next[editingIndex] = form
    } else {
      next = [...params, form]
    }
    setParams(next)
    setModalOpen(false)
    await persist(next)
  }

  // 保存运行配置到数据库
  const saveRunConfig = async () => {
    if (!id) return
    // 前端校验：env_requests / env_vars 必须是合法 JSON 对象
    for (const [, val, label] of [
      ['env_requests', envRequests, '环境要求'],
      ['env_vars', envVars, '环境变量'],
    ]) {
      if (val.trim()) {
        try {
          const parsed = JSON.parse(val)
          if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
            message.error(`${label}必须是 JSON 对象，如 {"python": ">=3.8"}`)
            return
          }
          for (const [k, v] of Object.entries(parsed)) {
            if (typeof v !== 'string') {
              message.error(`${label}的值必须是字符串，键"${k}" 的值为 ${typeof v}`)
              return
            }
          }
        } catch {
          message.error(`${label}不是合法 JSON`)
          return
        }
      }
    }
    try {
      await updateScript(Number(id), {
        working_dir: workingDir || null,
        env_vars: envVars || null,
        env_requests: envRequests || null,
        dependencies: dependencies.length ? JSON.stringify(dependencies) : null,
      })
      message.success('配置已保存')
      // 批次 AW ③：保存成功 → 刷新初值快照（否则「保存配置」按钮不消失）
      setCfgBase(cfgKey(workingDir, envVars, envRequests, dependencies))
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败')
    }
  }

  // 执行脚本
  const handleRun = async () => {
    if (!id || !currentScript) return
    
    // 高危脚本二次确认
    if (currentScript.dangerous) {
      Modal.confirm({
        title: '⚠️ 高危脚本确认',
        content: (
          <div>
            <p>您即将执行一个<strong>高危脚本</strong>：</p>
            <p style={{ fontFamily: 'monospace', background: 'var(--sh-panel2, #f5f5f5)', padding: 8, borderRadius: 4 }}>
              {currentScript.name}
            </p>
            <p>此脚本可能对系统造成不可逆的影响，请确认您了解脚本的功能。</p>
          </div>
        ),
        okText: '确认执行',
        cancelText: '取消',
        okButtonProps: { danger: true },
        onOk: () => executeScript(),
      })
      return
    }
    
    await executeScript()
  }

  // V5-F：把 handleRun 注册到 ref（供 Ctrl+Enter 全局快捷键调用）
  runRef.current = handleRun

  // 实际执行脚本的函数（withEnvConfirm 出现环境告警重试时传 true）
  const executeScript = async (confirmEnv = false) => {
    if (!id || !currentScript) return
    setActiveTab('output')

    try {
      setIsRunning(true)
      setRunOutput('')
      setRunStatus('running')
      
      // 构建参数对象（使用运行时输入值，不是默认值）
      const parameters: Record<string, any> = {}
      // 批次 BC：唯一输入源 = 右栏「运行参数」（`scripthub:run` 唯一派发点必带 detail.params）；
      // 原 `?? paramValues`（Web 页内输入，已废弃）兜底删除，缺值仍由下方 `?? p.default` 兜底
      const valsIn: Record<string, string> = extParamsRef.current ?? {}
      params.forEach(p => {
        const val = valsIn[p.name] ?? p.default ?? ''
        if (p.type === 'bool') {
          parameters[p.name] = val === 'true' || val === '1'
        } else {
          if (val !== '') parameters[p.name] = val
        }
      })
      
      // 解析环境变量
      let envVarsObj: Record<string, string> | undefined
      if (envVars) {
        try {
          envVarsObj = JSON.parse(envVars)
        } catch {
          message.error('环境变量格式错误，请使用JSON格式')
          setIsRunning(false)
          return
        }
      }
      
      // 调用执行API
      const result = await runScript({
        script_id: Number(id),
        parameters,
        working_dir: workingDir || undefined,
        env_vars: envVarsObj,
        timeout: extTimeoutRef.current || undefined,
        confirm_dangerous: true,
        confirm_env: confirmEnv,
        device_id: currentDeviceId || undefined,
        shell: (extShellRef.current ?? null) as any,
      })
      
      setCurrentRunId(result.data.id)
      message.success('脚本已开始执行')
      
      // 连接WebSocket获取实时输出
      connectWebSocket(result.data.id)
      
    } catch (error: any) {
      // 环境检测未达标：弹出确认，用户确认后带 confirm_env 重试
      const status = error?.response?.status
      const detail = error?.response?.data?.detail
      if (status === 409 && (detail?.checks || detail?.runtime)) {
        setIsRunning(false)
        setRunStatus('')
        // 本机 env 检测：逐项展示；远程运行时缺失：单条提示
        const isRemote = detail?.runtime
        Modal.confirm({
          title: isRemote ? '⚠️ 远程环境可能缺失运行时' : '⚠️ 环境检测未达标',
          content: (
            <div>
              {isRemote ? (
                <p style={{ margin: '4px 0' }}>
                  <strong>{detail.message}</strong>
                </p>
              ) : (
                (detail.checks as Array<{ name: string; required: string; actual: string | null; detail: string }>).map((c, i) => (
                  <p key={i} style={{ margin: '4px 0' }}>
                    <strong>{c.name}</strong>: {c.detail}
                  </p>
                ))
              )}
              <p style={{ color: '#888', marginTop: 8 }}>确认继续执行吗？</p>
            </div>
          ),
          okText: '确认执行',
          cancelText: '取消',
          okButtonProps: { danger: true },
          onOk: () => executeScript(true),
        })
        return
      }
      message.error(error?.response?.data?.detail?.message || '执行脚本失败')
      setIsRunning(false)
      setRunStatus('failed')
    }
  }

  // 连接WebSocket获取实时输出
  const connectWebSocket = (runId: number) => {
    const ws = new WebSocket(`${getWsBase()}/api/run/ws/${runId}`)
    let lastOutputLen = 0  // 追踪已显示的输出长度
    
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)
      // 后端每次发送完整输出，只追加新增部分
      const fullOutput = data.output || ''
      if (fullOutput.length > lastOutputLen) {
        setRunOutput(fullOutput)
        lastOutputLen = fullOutput.length
        window.dispatchEvent(new CustomEvent('scripthub:output-updated')) // V5-G：工作区历史刷新
      }
      if (data.status) {
        setRunStatus(data.status)
        if (data.status !== 'running') {
          setIsRunning(false)
          ws.close()
          // 批次 BA：终态到达即刷新右栏历史（覆盖「结束时无新输出」→ 上方 length 判据不触发的情况）。
          window.dispatchEvent(new CustomEvent('scripthub:output-updated'))
        }
      }
    }
    
    ws.onerror = () => {
      message.error('WebSocket连接错误')
      setIsRunning(false)
      setRunStatus('failed')
    }
    
    ws.onclose = () => {
      if (isRunning) {
        setIsRunning(false)
      }
    }
  }

  // 终止脚本
  const handleKill = async () => {
    if (!currentRunId) return
    
    try {
      await killRun(currentRunId)
      message.success('脚本已终止')
      setIsRunning(false)
      setRunStatus('killed')
      // 批次 BA：终止后立即让右栏「近 5 次历史」重取 —— 此前只改本地 state，
      // 而后端刚把该行置 killed，历史行仍显示「运行中」。
      window.dispatchEvent(new CustomEvent('scripthub:output-updated'))
      
    } catch (error) {
      message.error('终止脚本失败')
    }
  }

  // 批次 AW ②：注册到 ref（参数栏「终止」经 scripthub:kill 调用）。赋值点在定义之后 —— handleKill 是
  // 每渲染重建的普通函数，ref 转发可让监听器只挂一次且始终调到最新闭包（同上方 runRef 模式）。
  killRef.current = handleKill

  // 清空输出
  const handleClearOutput = () => {
    setRunOutput('')
  }

  // 手动环境检测（批次 AO：带上**当前设备** —— 依赖分析 tab 与配置 tab 的「环境检测」都走这里。
  // 此前不传 device_id → 后端一律探 Server 本机 → Windows 设备上永远报 unix，脚本被误判不通过。）
  const handleEnvCheck = async () => {
    if (!id) return
    try {
      const { data } = await envCheckScript(Number(id), currentDeviceId)
      const devLabel = currentDeviceId ? '当前设备' : '本机'
      Modal.info({
        title: `环境检测结果（${devLabel}）`,
        width: 600,
        content: (
          <div>
            {/* conclusive=false：设备侧没有新鲜探测结论 → checks 为空，**一项都没判**，
                不是「不通过」。别把它渲染成「无环境要求」（那会让人以为检测通过）。 */}
            {!data.conclusive && (
              <p style={{ color: '#d46b08', margin: 0 }}>{data.note || '未取得探测结论'}</p>
            )}
            {data.conclusive && data.checks.length === 0 && (
              <p style={{ color: '#888' }}>无环境要求</p>
            )}
            {data.checks.map((c, i) => (
              <div
                key={i}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 8,
                  padding: '6px 0',
                  borderBottom: i < data.checks.length - 1 ? '1px solid var(--sh-border, #f0f0f0)' : 'none',
                }}
              >
                <Tag color={c.ok ? 'success' : 'error'}>{c.ok ? '通过' : '未通过'}</Tag>
                <div>
                  <div><strong>{c.name}</strong></div>
                  <div style={{ color: '#666', fontSize: 12 }}>{c.detail}</div>
                </div>
              </div>
            ))}
          </div>
        ),
      })
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '环境检测失败')
    }
  }

  // 手动依赖分析（函数声明：上面的 tab 按需取数 effect 要直接调用它，声明提升免去 ref 转发）
  async function handleDeps() {
    if (!id) return
    setDepsLoading(true)
    try {
      const { data } = await getScriptDeps(Number(id))
      setDeps(data.deps || [])
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '依赖分析失败')
    } finally {
      setDepsLoading(false)
    }
  }

  const commandPreview = currentScript
    ? buildCommand(currentScript.name, currentScript.category, params)
    : ''

  if (!currentScript) return <Spin size="large" />

  // V5-G：五 tab 内容拆分（desktop=Tabs / web=平铺）。生成 JSX 的辅助函数复用原 Card 结构。
  // v2.16 §0.5：基本信息板块的四个字段 JSX 抽成变量——桌面折叠段（.kvmini）与 Web 平铺 Card（.kv 网格）共用（改容器不改字段）
  const typeField = (
    <>
      <span className="tag">{currentScript.category}</span>
      {currentScript.dangerous && <span className="tag" style={{ color: 'var(--err)', borderColor: '#5a3a3a' }}>⚠ 高危</span>}
    </>
  )
  // 批次 I-3：去掉「（改动即移动脚本）」提示字样；dirField 只剩 DirSelect 本身（不再套多余 Fragment）
  const dirField = (
    <DirSelect
      value={currentScript.relative_path.split(/[/\\]/).slice(0, -1).join('/')}
      onChange={async (dir) => {
        try {
          const { data } = await moveScript(currentScript.id, dir)
          useScriptStore.setState({ currentScript: data })
          message.success('已移动到目录 ' + (dir || '(根目录)'))
        } catch (err: any) {
          message.error(err?.response?.data?.detail || '移动失败')
        }
      }}
    />
  )
  const dangerField = (
    <>
      <span className={`switch${currentScript.dangerous ? '' : ' off'}`} style={{ cursor: 'pointer' }}
        onClick={async () => {
          const checked = !currentScript.dangerous
          try {
            await updateScript(currentScript.id, { dangerous: checked })
            useScriptStore.setState({ currentScript: { ...currentScript, dangerous: checked } })
            message.success(checked ? '已标记为高危脚本' : '已取消高危标记')
          } catch { message.error('修改失败') }
        }} />{' '}
      <span className="mono">{currentScript.dangerous ? '已开启（执行前二次确认）' : '未开启'}</span>
    </>
  )
  const tagsField = (
    <>
      {/* G2-5：标签行只保留 TagPicker 本身（选中项在其输入框内以芯片呈现），不再并列渲染一遍芯片 */}
      <TagPicker
        key={tagsTick}
        value={scriptTagIds}
        onChange={async (vals) => {
          setScriptTagIds(vals)
          try {
            await setScriptTags(currentScript.id, vals)
            const { data } = await getTags()
            const names = data.items.filter((t) => vals.includes(t.id)).map((t) => t.name)
            useScriptStore.setState({
              currentScript: { ...currentScript, tags: names },
            })
            setAllTags(data.items)
          } catch {
            message.error('标签更新失败')
          }
        }}
      />
    </>
  )

  // v2.16 §0.5：单栏折叠分段 + sticky 命令预览（三段标题行高 38；「参数配置」默认展开）
  // 批次 AW ③：extra = 标题行右侧的就地动作（目前只有「运行环境与随传文件」段用：有改动才出现的保存按钮）
  const foldHead = (title: string, summary: string, key: 'basic' | 'params' | 'env', extra?: ReactNode) => (
    <div className="fh" style={{ cursor: 'pointer' }} data-testid={`fh-${key}`}
      onClick={() => setFoldOpen((f) => ({ ...f, [key]: !f[key] }))}>
      <b>{title}</b>
      <span className="sm">{summary}</span>
      <span className="ar">{foldOpen[key] ? '折叠 ▴' : '展开 ▾'}</span>
      {extra}
    </div>
  )

  const jsonKeys = (raw: string): Record<string, unknown> => {
    try { const o = JSON.parse(raw || '{}'); return o && typeof o === 'object' ? o : {} } catch { return {} }
  }
  const envVarKeys = Object.keys(jsonKeys(envVars))
  const envReqObj = jsonKeys(envRequests)
  const envReqText = Object.keys(envReqObj).length
    ? Object.entries(envReqObj).map(([k, v]) => `${k}${v}`).join(' ')
    : '无'
  const requiredCount = params.filter((p) => p.required).length
  const dirName = currentScript.relative_path.split(/[/\\]/).slice(0, -1).join('/') || '根目录'
  const basicSummary = [
    currentScript.category,
    currentScript.dangerous ? '高危' : '非高危',
    dirName,
    (currentScript.tags || []).length ? `${currentScript.tags.length} 个标签` : '无标签',
    dependencies.length ? `${dependencies.length} 个随传文件` : '无随传文件',
  ].join(' · ')

  const copyText = async (text: string) => {
    try { await navigator.clipboard.writeText(text); message.success('已复制') }
    catch { message.error('复制失败') }
  }

  const basicFold = (
    <div className="fold" data-testid="fold-basic">
      {foldHead('基本信息', basicSummary, 'basic')}
      {/* hidden 而非不渲染：三段 .fb 恒在 DOM，折叠态 display:none（便于探针断言） */}
      <div className="fb" hidden={!foldOpen.basic}>
        <div className="pathbar">
          <span className="p" title={currentScript.path}>{currentScript.path}</span>
          <span className="cp" onClick={() => copyText(currentScript.path)}>复制</span>
        </div>
        <div className="kvmini">
          <div><span>类型</span>{typeField}</div>
          <div><span>所在目录</span>{dirField}</div>
          <div><span>危险脚本</span>{dangerField}</div>
          <div><span>标签</span>{tagsField}</div>
        </div>
      </div>
    </div>
  )

  const columns = [
    { title: '参数名', dataIndex: 'name', key: 'name' },
    { title: '类型', dataIndex: 'type', key: 'type', width: 80 },
    { title: '默认值', dataIndex: 'default', key: 'default',
      render: (v: string | null) => v ?? '-' },
    { title: '必填', dataIndex: 'required', key: 'required', width: 60,
      render: (v: boolean) => v ? '是' : '否' },
    { title: '描述', dataIndex: 'description', key: 'description',
      render: (v: string | null) => v ?? '-' },
    {
      title: '操作', key: 'actions', width: 120,
      render: (_: unknown, _record: ParamDef, idx: number) => (
        <Space>
          <Button size="small" onClick={() => openEdit(idx)}>编辑</Button>
          <Popconfirm title="确认删除?" onConfirm={() => handleDelete(idx)}>
            <Button size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  // 原型 §0 输出 tab：**无卡壳**——输出框直接铺满 tab（.out 自带 --sh-term 底 / r6 / mono 11.5）
  const outputCard = (
    <div style={{ flex: 1, minHeight: 0, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
      <Terminal output={runOutput} status={runStatus} onClear={handleClearOutput} />
    </div>
  )

  const contentCard = (
    <div className="dcard" style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
      {scriptContent !== null && scriptLanguage ? (
        editMode ? (
          <textarea
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            spellCheck={false}
            style={{
              // 桌面形态：.dcard 是唯一的框，textarea 只留 term 底色（去 border/radius，恢复内边距）
              width: '100%', height: 420, background: 'var(--sh-term, #191a1f)', color: '#d5d9e0',
              border: 'none', borderRadius: 0, padding: '10px 12px',
              fontFamily: 'var(--sh-mono, ui-monospace, monospace)', fontSize: 12.5, lineHeight: 1.55, resize: 'vertical',
            }}
          />
        ) : (
          <>
            <CodeViewer code={scriptContent} language={scriptLanguage} />
          </>
        )
      ) : (
        <Spin />
      )}
      {/* v2.8（用户）：元信息行右下承载 编辑/保存 —— 不再单开标题行 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="mono">只读预览 · {scriptContent ? scriptContent.split('\n').length : 0} 行 · {scriptLanguage}</span>
        {/* 只读行尾标记（不可点、无 hover、非按钮）：LF / CRLF / 混合；内容未加载时 eolLabel=null → 不渲染 */}
        {eolLabel && (
          <span className="mono" data-testid="eol-marker" style={{ cursor: 'default' }}>· {eolLabel}</span>
        )}
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          {editMode ? (
            <>
              <span className="btn" style={{ cursor: 'pointer' }} onClick={() => { setEditContent(scriptContent ?? ''); setEditMode(false) }}>取消</span>
              <span className="btn pri" style={{ cursor: 'pointer', opacity: saving ? .6 : 1 }} onClick={handleSaveContent}>{saving ? '保存中…' : '保存'}</span>
            </>
          ) : (
            <span className="btn" style={{ cursor: 'pointer' }} onClick={() => { setEditContent(scriptContent ?? ''); setEditMode(true) }}>✎ 编辑</span>
          )}
        </span>
      </div>
    </div>
  )

  // v2.16 §0.5：参数配置 = 折叠段（默认展开；段框描边 #3d4763）
  const paramsFold = (
    <div className="fold" data-testid="fold-params" style={{ borderColor: '#3d4763' }}>
      {foldHead('参数配置', `${params.length} 个参数 · ${requiredCount} 个必填`, 'params')}
      <div className="fb" hidden={!foldOpen.params}>
        <div style={{ display: 'flex', gap: 8, padding: '10px 0 8px', alignItems: 'center' }}>
          <span className="btn" style={{ cursor: 'pointer' }} onClick={handleParse}>⚡ 自动解析</span>
          <span className="btn pri" style={{ cursor: 'pointer' }} onClick={openAdd}>+ 手动添加</span>
          <span className="mono" style={{ marginLeft: 'auto' }}>按 shebang / argparse 自动识别</span>
        </div>
        <Table
          dataSource={params.map((p, i) => ({ ...p, key: i }))}
          columns={columns}
          pagination={false}
          size="small"
          locale={{ emptyText: '暂无参数，点击"自动解析"或"手动添加"' }}
        />
      </div>
    </div>
  )

  // V5-G（SPEC §2.3）：运行环境与随传文件字段——桌面「脚本配置」tab 与 Web「运行配置」Card 共用同一批 JSX（复用非复制）
  const envFields = (
    <>
      <div>
        <label>随传文件（远程执行时随脚本上传的本地文件）</label>
        <Select
          mode="multiple"
          style={{ width: '100%' }}
          value={dependencies}
          onChange={(v) => setDependencies(v as string[])}
          placeholder="选择随传的本地文件（用于 source/import 等依赖；敏感文件被后端拒绝）"
          options={depCandidates.map((f) => ({ value: f, label: f }))}
          filterOption={(input, option) => (option?.label ?? '').toLowerCase().includes(input.toLowerCase())}
        />
      </div>
      <div>
        <label>工作目录</label>
        <Input
          placeholder="留空则使用脚本所在目录"
          value={workingDir}
          onChange={(e) => setWorkingDir(e.target.value)}
        />
      </div>
      <div>
        <label>环境变量（JSON格式）</label>
        <Input.TextArea
          placeholder='{"KEY": "value"}'
          value={envVars}
          onChange={(e) => setEnvVars(e.target.value)}
          rows={3}
        />
      </div>
      <div>
        <label>环境要求（JSON格式，写法见占位符）</label>
        <Input.TextArea
          placeholder='{"python": ">=3.8", "node": ">=18"}'
          value={envRequests}
          onChange={(e) => setEnvRequests(e.target.value)}
          rows={3}
        />
      </div>
    </>
  )

  const tabOutput = outputCard
  const tabPreview = contentCard
  // V5-G（SPEC §2.3 / 原型 §0.3）：依赖检测 = 内联 deps 表格（原 Modal 取消）+ 重新检测 + 环境检测
  const tabDeps = (
    <div className="dcard" style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <b style={{ fontSize: 13 }}>依赖检测</b>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <span className="btn" style={{ cursor: 'pointer', opacity: depsLoading ? .6 : 1 }} onClick={handleDeps}>{depsLoading ? '检测中…' : '⟳ 重新检测'}</span>
          <span className="btn" style={{ cursor: 'pointer' }} onClick={handleEnvCheck}>环境检测</span>
        </span>
      </div>
      <div style={{ color: 'var(--muted)', fontSize: 12.5 }}>
        检测脚本声明的依赖（requirements.txt / package.json / pyproject.toml）在目标机的安装状态；远程执行时随传文件在「脚本配置」tab 选择。
      </div>
      <table>
        <thead>
          <tr><th>包名</th><th style={{ width: 130 }}>版本约束</th><th style={{ width: 190 }}>状态</th></tr>
        </thead>
        <tbody>
          {deps.map((d) => (
            <tr key={d.name}>
              <td className="mono" style={{ color: 'var(--text)' }}>{d.name}</td>
              <td className="mono">{d.constraint}</td>
              <td><span className={d.installed ? 'ok' : 'err'}>● {d.installed ? `已装 ${d.installed_version}` : '未安装'}</span></td>
            </tr>
          ))}
          {deps.length === 0 && (
            <tr><td colSpan={3} style={{ color: 'var(--muted)' }}>
              {depsLoading ? '检测中…' : '脚本目录下无依赖文件（requirements.txt / package.json / pyproject.toml）'}
            </td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
  // V5-G（SPEC §2.3 / 原型 §0.4）：定时配置 = 本脚本任务列表（只读，数据同源 /api/schedules）+ 新建任务跳转
  const tabSchedule = (
    <div className="dcard" style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <b style={{ fontSize: 13 }}>定时配置</b>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <span className="btn pri" style={{ cursor: 'pointer' }} onClick={() => navigate(`/schedules?script_id=${id}`)}>+ 新建任务</span>
          <span className="btn" style={{ cursor: 'pointer' }} onClick={() => navigate('/schedules')}>前往定时调度</span>
        </span>
      </div>
      <table>
        <thead>
          <tr>
            <th>名称</th><th style={{ width: 130 }}>触发</th><th style={{ width: 150 }}>执行位置</th>
            <th style={{ width: 170 }}>下次触发</th><th style={{ width: 140 }}>上次结果</th><th style={{ width: 60 }}>启用</th>
          </tr>
        </thead>
        <tbody>
          {scriptSched.map((r: ScheduleItem) => {
            const ok = r.last_run?.status === 'success'
            const failed = !!r.last_run && !ok
            const hm = r.last_run?.started_at
              ? new Date(r.last_run.started_at).toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit' })
              : ''
            return (
              <tr key={r.id} style={r.enabled ? undefined : { color: 'var(--muted)' }}>
                <td>{r.name}</td>
                <td className="mono">{r.cron_expr || `每 ${r.interval_seconds}s`}</td>
                <td><span className="tag">{r.exec_location === 'device' ? `下放·${r.device_type === 'windows' ? 'schtasks' : 'crontab'} ⇗` : '本机调度'}</span></td>
                <td className="mono" style={r.next_run_at ? undefined : { color: 'var(--muted)' }}>
                  {r.next_run_at ? new Date(r.next_run_at).toLocaleString('zh-CN', { hour12: false }) : '—'}
                </td>
                <td className={!r.last_run ? 'mono' : ok ? 'ok' : failed ? 'err' : ''} style={!r.last_run ? { color: 'var(--muted)' } : undefined}>
                  {!r.last_run ? '从未运行'
                    : ok ? `${hm} · exit ${r.last_run.exit_code ?? 0}`
                      : `${hm} · exit ${r.last_run.exit_code ?? '-'}`}
                </td>
                <td style={r.enabled ? undefined : { color: 'var(--muted)' }}>{r.enabled ? '启用' : '停用'}</td>
              </tr>
            )
          })}
          {scriptSched.length === 0 && (
            <tr><td colSpan={6} style={{ color: 'var(--muted)' }}>
              {schedLoading ? '加载中…' : '此脚本暂无定时任务——点右上「新建任务」创建'}
            </td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
  // v2.16 §0.5：单栏折叠分段（标题行 38 / 段间距 10）+ 底部 sticky 命令预览（随参数实时刷新）
  // 批次 AW ③：保存入口从 tab 条移到本段标题行（只有本段的 4 个输入框需要显式保存；其余字段即改即存）。
  // 批次 AY：timeout 输入框已撤销（改为右栏唯一入口 + 执行时落库），故不再进 cfgKey。
  // 按钮**只在有改动时出现**，样式沿用既有 `.btn`（不用 .btn pri：主动作是「执行」）。
  const cfgDirty = cfgBase !== null && cfgBase !== cfgKey(workingDir, envVars, envRequests, dependencies)
  const envFold = (
    <div className="fold" data-testid="fold-env">
      {foldHead('运行环境与随传文件',
        `随传 ${dependencies.length} · 工作目录 ${workingDir ? '已设' : '未设'} · 环境变量 ${envVarKeys.length} · 环境要求 ${envReqText}`,
        'env',
        cfgDirty && (
          <span className="btn" data-testid="env-save" style={{ cursor: 'pointer', padding: '2px 10px', fontSize: 12 }}
            onClick={(e) => { e.stopPropagation(); saveRunConfig() }}>保存配置</span>
        ))}
      <div className="fb" hidden={!foldOpen.env}>{envFields}</div>
    </div>
  )

  const cmdSticky = commandPreview && (
    <div className="sticky" data-testid="cmd-sticky">
      <div className="cmd" data-testid="cmd-preview">
        <span style={{ color: 'var(--muted)' }}>$ </span>{commandPreview}
        <span style={{ float: 'right', color: 'var(--muted)', cursor: 'pointer' }}
          onClick={() => copyText(commandPreview)}>复制</span>
      </div>
    </div>
  )

  const tabConfig = (
    <>
      {basicFold}
      {paramsFold}
      {envFold}
      {cmdSticky}
    </>
  )

  return (
    <div style={{ flex: 1, overflow: 'auto' }}>
      <div className="dbody">
        <div className="tabs">
          {TABS.map(([k, label]) => (
            <span key={k} className={`tb${activeTab === k ? ' on' : ''}`} style={{ cursor: 'pointer' }} onClick={() => setActiveTab(k)}>{label}</span>
          ))}
          {/* v2.8：运行控件搬到右栏（ScriptWorkspace 参数栏）时，保存配置/终止/新窗口打开 三个动作被留在已废弃的 Web 平铺块里 → 桌面形态不可达。
              批次 AW：①「终止」→ 右栏参数栏（与「执行」同行，运行态才现）；②「保存配置」→「脚本配置」tab「运行环境与随传文件」段标题行（有改动才现）。
              批次 AX ①（用户拍板）：③「新窗口打开」**整删** —— 已确证在真壳内不可用（点击无反应）：capabilities 未授权
              core:webview:allow-create-webview-window（既不在 core:webview:default 也不在 default.json）→ ACL 直接拒；
              该文件的 windows:["main"] 使新窗口零 IPC；端口只 eval 给 main → 新窗口 apiBase 为空。
              AW 批为挂「保存配置/终止」加的 marginLeft:auto 右侧容器随之清掉（末尾那个包装只剩它一个）。
              至此 tab 条**只剩 5 个 tab 芯片**（TABS），无右侧动作容器。 */}
        </div>
        <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
          {activeTab === 'output' ? tabOutput : activeTab === 'preview' ? tabPreview
            : activeTab === 'deps' ? tabDeps : activeTab === 'schedule' ? tabSchedule : tabConfig}
        </div>
      </div>

      {/* v2.16 §6.7：标签管理浮层（脚本配置 tab 基本信息「管理」入口） */}
      <TagManagerPalette open={tagPanelOpen} onClose={() => setTagPanelOpen(false)} />

      <Modal
        title={editingIndex !== null ? '编辑参数' : '添加参数'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        destroyOnClose
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <label>参数名</label>
            <Input
              placeholder="--target"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </div>
          <div>
            <label>类型</label>
            <Select
              style={{ width: '100%' }}
              value={form.type}
              onChange={(v) => setForm({ ...form, type: v })}
              options={Object.entries(TYPE_LABELS).map(([k, v]) => ({ value: k, label: v }))}
            />
          </div>
          <div>
            <label>默认值</label>
            <Input
              value={form.default ?? ''}
              onChange={(e) => setForm({ ...form, default: e.target.value || null })}
            />
          </div>
          <div>
            <label>必填</label>
            <Switch
              checked={form.required}
              onChange={(v) => setForm({ ...form, required: v })}
            />
          </div>
          <div>
            <label>描述</label>
            <Input
              value={form.description ?? ''}
              onChange={(e) => setForm({ ...form, description: e.target.value || null })}
            />
          </div>
        </div>
      </Modal>

    </div>
  )
}
