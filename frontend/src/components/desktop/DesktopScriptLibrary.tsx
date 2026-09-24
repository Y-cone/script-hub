/**
 * V5-G 桌面形态 · §1 脚本库（1:1 复刻 docs/v5g-pages-mockup.html §1）
 *
 * 结构（原型定案）：目录树 200px + 列表 1fr + 右摘要卡 280px
 *   左：全部 N（选中态 pri/float）+ 目录树（▾/▸ + 计数）+ 标签区（tag 芯片带计数）
 *   中：.filterbar（搜索 180px / 类型 / 右侧「N 个脚本」）+ 原生 <table>（状态点列 / 名称 / 类型 / 最近运行）
 *   右：脚本名 + 标签 + .kv 行（目录 / 最近运行 / 定时任务）+ ▷执行·定时·导出 + 最近输出（term 框）
 * 视觉全部来自 styles/proto.css（原型样式层），不使用 antd 视觉组件。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { message, Modal } from 'antd'
import { getScripts, getScriptDirs, getTags, getSchedules, getRunHistory, runScript, exportScript, deleteScript, uploadScript, importScript } from '../../services/api'
import { saveBinaryFile, showContextMenu, openBinaryFile } from '../../services/desktop'
import { useScriptStore } from '../../stores/scriptStore'
import { useDeviceContext } from '../../stores/deviceContext'
import { durationText } from '../../utils/format'
import type { ScriptItem } from '../../services/api'
import TagManagerPalette from './TagManagerPalette'
import Sel from './Sel'

const CATEGORY_LABEL: Record<string, string> = { python: 'python', shell: 'shell', bat: 'bat', powershell: 'powershell' }

/** 批次 M-2：脚本类型 → 可运行平台。与后端 envcheck.PLATFORM_RULES 同表（后端那份是唯一权威）。
 *  扩展名由 scanner.EXTENSION_MAP 归到 category，故按 category 判定等价于 .py/.sh/.bat/.ps1 逐个判定。 */
const PLATFORM_BY_CATEGORY: Record<string, 'any' | 'unix' | 'windows'> = {
  python: 'any',
  shell: 'unix',
  bat: 'windows',
  powershell: 'windows',
}

/** 批次 AI：非原生平台上的例外运行时（与后端 envcheck.ALT_RUNTIME_RULES 同表：类型 → 可执行名）。
 *  只有 .sh 有这条例外——Windows 上装了 Git Bash/MSYS2/WSL 就能跑 bash 脚本；
 *  .bat/.ps1 在 Unix 没有替代运行时，仍判红。 */
const ALT_RUNTIME_BY_CATEGORY: Record<string, string> = {
  shell: 'bash',
}

/** 本机平台（与 ScriptWorkspace 的 LOCAL_SHELLS 同判据：后端以 sys.platform 为准） */
const LOCAL_PLATFORM: 'windows' | 'unix' =
  typeof navigator !== 'undefined' && /Windows/i.test(navigator.userAgent) ? 'windows' : 'unix'

/** SPEC §2.3 v2.16：列表标签列最多 2 个芯片，超出显示 +N */
const TAG_CELL_MAX = 2

/** 相对时间（原型 §1 列表「2h 前」） */
function timeAgo(iso?: string | null): string {
  if (!iso) return '从未'
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return '刚刚'
  if (s < 3600) return `${Math.floor(s / 60)}m 前`
  if (s < 86400) return `${Math.floor(s / 3600)}h 前`
  if (s < 86400 * 7) return `${Math.floor(s / 86400)}d 前`
  return '很久前'
}

function statusSymbol(s?: string | null): { sym: string; cls: string } {
  if (s === 'success') return { sym: '', cls: 'ok' }
  if (s === 'failed' || s === 'timeout') return { sym: '', cls: 'err' }
  if (s === 'running') return { sym: '▶', cls: 'warn' }
  return { sym: '', cls: '' }
}

export default function DesktopScriptLibrary() {
  const navigate = useNavigate()
  const { currentDeviceId, deviceNames, devicePlatforms, deviceRuntimes } = useDeviceContext()
  const [items, setItems] = useState<ScriptItem[]>([])
  const [total, setTotal] = useState(0)
  const [catalog, setCatalog] = useState<ScriptItem[]>([])   // 全量（目录/标签计数用）
  // 左侧「全部」计数：取全量那一次请求的 total，与分页/筛选无关（catalog.length 在 >100 个脚本时偏小）
  const [catalogTotal, setCatalogTotal] = useState(0)
  // 批次 AE：与运行历史同一套分页（page/pageSize/pages + ‹ n / N ›）。
  // 每页 20 = 运行历史的量：两页行高同为 37px、可见高度同为「过滤行 + 表头」剩下的那截，
  // 列表页统一一个尺寸，免得同一产品里两处翻页手感不同。
  const [page, setPage] = useState(1)
  const pageSize = 20
  const [dirs, setDirs] = useState<string[]>([])
  const [tags, setTags] = useState<{ id: number; name: string; color?: string }[]>([])
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('')
  const [dir, setDir] = useState('')
  // SPEC §2.3 v2.16：筛选行「全部标签 ▾」多选 + 已选芯片（✕ 移除）；筛选作用于服务端 → total 与行数据同源
  const [tagFilter, setTagFilter] = useState<number[]>([])
  const [tagPanelOpen, setTagPanelOpen] = useState(false)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [selected, setSelected] = useState<ScriptItem | null>(null)
  const [sched, setSched] = useState({ total: 0, enabled: 0 })
  const [output, setOutput] = useState('')
  const [running, setRunning] = useState(false)
  const [loading, setLoading] = useState(false)
  const [reload, setReload] = useState(0)

  // 列表（服务端筛选）
  // V5-G：上传 / 导入 / 扫描（原型 §1 filterbar 以 .tag 芯片承载操作；桌面直调系统对话框，无 antd 视觉）
  const doScan = useScriptStore((s) => s.doScan)
  const fileRef = useRef<HTMLInputElement | null>(null)
  const refresh = () => { setReload((t) => t + 1); loadCatalog() }
  const onUpload = async (f: File) => {
    try { await uploadScript(f, dir || ''); message.success(`已上传 ${f.name}`); refresh() }
    catch (e: any) { message.error(e?.response?.data?.detail || '上传失败') }
  }
  const onImport = async () => {
    const f = await openBinaryFile()
    if (!f) return
    try { const { data } = await importScript(f); message.success(data?.message || '导入成功'); refresh(); getScriptDirs().then((r) => setDirs(r.data.directories || [])).catch(() => {}) }
    catch (e: any) { message.error(e?.response?.data?.detail || '导入失败') }
  }
  const onScan = async () => {
    const r = await doScan()
    getScriptDirs().then((x) => setDirs(x.data.directories || [])).catch(() => {})
    message.success(`扫描完成：新增 ${r.added}，更新 ${r.updated}，删除 ${r.removed}`)
    refresh()
  }

  useEffect(() => {
    setLoading(true)
    getScripts({
      page, page_size: pageSize, search: search || undefined, category: category || undefined,
      directory: dir || undefined,
      // 标签筛选：后端 AND 语义（同时命中所有 tag_ids），total 与 items 走同一 where
      tag_ids: tagFilter.length ? tagFilter.join(',') : undefined,
    })
      .then((res) => {
        const list: ScriptItem[] = res.data.items || []
        setItems(list); setTotal(res.data.total || 0)
        // 批次 AI ②：轮询重拉时按 id 把选中项换成新快照 → 右栏「最近运行」跟着列表一起变
        //（否则列表绿了、右栏还写着「运行中」）。选中项被筛选/翻页甩出本页时保留旧快照。
        setSelected((p) => (p ? list.find((x) => x.id === p.id) ?? p : p))
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [page, search, category, dir, tagFilter, reload])

  const pages = Math.max(1, Math.ceil(total / pageSize))

  // 全量目录/标签（计数用）—— 与分页无关：固定 page=1/size=100，跟着 page 走会让左侧列表随翻页变少
  const loadCatalog = () => {
    // ponytail: 上限 100（后端 le=100）；超 100 个脚本时目录/标签计数会偏小
    getScripts({ page: 1, page_size: 100 })
      .then((res) => { setCatalog(res.data.items || []); setCatalogTotal(res.data.total || 0) })
      .catch(() => {})
    getScriptDirs().then((res) => setDirs(res.data.directories || [])).catch(() => {})
    getTags().then((res) => setTags(res.data.items || [])).catch(() => {})
  }
  useEffect(loadCatalog, [])

  // v2.16：管理浮层里改名/改色/新增/删除后 → 列表 + 标签计数立即刷新（不留本地缓存）
  // 批次 BD：F5 刷新（useGlobalShortcuts 派发 scripthub:refresh）走同一路 —— 重取列表 + 全量目录/标签计数
  useEffect(() => {
    const reloadAll = () => { setReload((t) => t + 1); loadCatalog() }
    window.addEventListener('scripthub:tags-changed', reloadAll)
    window.addEventListener('scripthub:refresh', reloadAll)
    return () => {
      window.removeEventListener('scripthub:tags-changed', reloadAll)
      window.removeEventListener('scripthub:refresh', reloadAll)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 选中脚本 → 定时任务统计 + 最近输出首行（原型 §1 摘要卡）
  // 批次 AI ②：这一路（含 :151 的 getRunHistory）由 loadOutput 复用给轮询，避免"列表绿了、右栏还在运行中"。
  // 依赖收窄到 selected?.id：轮询会把 selected 换成新快照，若还依赖整个对象会每 2.5s 重拉一次定时任务统计。
  const selectedIdRef = useRef<number | null>(null)
  const loadOutput = useCallback((id?: number | null) => {
    if (!id) { setOutput(''); return }
    selectedIdRef.current = id
    getRunHistory({ script_id: id, page: 1, page_size: 1, preview: true }).then((res) => {
      if (selectedIdRef.current !== id) return   // 期间切了脚本 → 丢弃过期响应（同原来的 cancelled 守卫）
      const line = (res.data.items?.[0]?.output || '').split('\n').find((l: string) => l.trim())
      setOutput(line ? line.trim() : '')
    }).catch(() => {})
  }, [])

  useEffect(() => {
    const id = selected?.id
    if (!id) { setSched({ total: 0, enabled: 0 }); setOutput(''); return }
    let cancelled = false
    getSchedules().then((res) => {
      if (cancelled) return
      const mine = (res.data.items || []).filter((s: any) => s.script_id === id)
      setSched({ total: mine.length, enabled: mine.filter((s: any) => s.enabled).length })
    }).catch(() => {})
    loadOutput(id)
    return () => { cancelled = true }
  }, [selected?.id, loadOutput])

  // 批次 AI ②：列表/右栏的「运行中」自刷新。写法照 DesktopRunHistory.tsx:64-69（同一套模式，不另造）：
  // 后端在**执行结束时**就已把 status/finished_at/duration 落库，缺的只是本页不会重拉 → 状态一直停在
  // 「运行中」，得切页或下次执行才变。有 running 才起表，全进终态立刻停表。
  // 重拉只走 reload（page/search/category/dir/tagFilter/selected 全不动）→ 轮询不打断用户操作。
  const hasRunning = items.some((s) => s.last_run?.status === 'running')
  useEffect(() => {
    if (!hasRunning) return
    const timer = setInterval(() => {
      setReload((t) => t + 1)            // 列表 + 右栏（selected 按 id 对齐新快照）
      loadOutput(selectedIdRef.current)  // :151 那一路最近输出
    }, 2500)
    return () => clearInterval(timer)
  }, [hasRunning, loadOutput])

  // 目录树：按 '/' 层级切成可折叠列表
  const tree = useMemo(() => {
    const rows: { name: string; path: string; depth: number; hasChild: boolean; count: number }[] = []
    const push = (prefix: string, depth: number) => {
      const children = new Set<string>()
      for (const d of dirs) {
        const seg = d.split('/').filter(Boolean)
        if (seg.length > depth && seg.slice(0, depth).join('/') === prefix) children.add(seg[depth])
      }
      for (const c of [...children].sort()) {
        const path = prefix ? `${prefix}/${c}` : c
        const hasChild = dirs.some((d) => d.startsWith(path + '/'))
        const count = catalog.filter((s) => (s.relative_path || '').replace(/^\/+/, '').startsWith(path + '/') || (s.relative_path || '').replace(/^\/+/, '').startsWith(path)).length
        rows.push({ name: c, path, depth, hasChild, count })
        if (hasChild && expanded[path] !== false) push(path, depth + 1)
      }
    }
    push('', 0)
    return rows
  }, [dirs, expanded, catalog])

  const tagCount = (name: string) => catalog.filter((s) => (s.tags || []).includes(name)).length

  /** 标签名 → 颜色（芯片色点；未知标签回落 --sh-dim） */
  const tagColor = (name: string) => tags.find((t) => t.name === name)?.color || 'var(--sh-dim)'

  /** 批次 M-2：可用性按「顶栏当前设备」判定，而不是后端按本机算出来的 available。
   *  远程设备只判平台（运行时探测要连设备，列表页不做）；本机才叠后端环境检测结果。
   *  devicePlatforms 由设备上下文（devices-changed 事件刷新）提供，未加载到时按 unix 兜底。
   *
   *  批次 AI（三态）：**平台族不符 ≠ 不可运行**。`.sh` 在 Windows 上由 Git Bash/MSYS2/WSL 的
   *  bash 跑（后端 envcheck.ALT_RUNTIME_RULES 同口径），原来一律标红是错的 → 改中性态；
   *  只有拿得到实测结论时才收紧成绿/红，拿不到就保持中性（不许因缺数据而标红）。 */
  const curPlatform: 'windows' | 'unix' = currentDeviceId
    ? devicePlatforms[currentDeviceId] || 'unix'
    : LOCAL_PLATFORM
  const curDeviceLabel = currentDeviceId
    ? `${deviceNames[currentDeviceId] || `设备#${currentDeviceId}`}（${curPlatform === 'windows' ? 'Windows' : 'Unix'}）`
    : `本机（${curPlatform === 'windows' ? 'Windows' : 'Unix'}）`
  const availOf = (s: ScriptItem): { cls: string; sym: string; title: string } => {
    const need = PLATFORM_BY_CATEGORY[s.category] ?? 'any'
    const ext = s.extension || s.category
    if (need !== 'any' && need !== curPlatform) {
      const alt = ALT_RUNTIME_BY_CATEGORY[s.category]
      // 无替代运行时（.bat/.ps1 在 Unix 设备）→ 根本跑不了，仍红
      if (!alt) {
        return { cls: 'err', sym: '●', title: `${curDeviceLabel} 上不可运行：${ext} 仅支持 ${need === 'windows' ? 'Windows' : 'Unix'}` }
      }
      // 有替代运行时（.sh + Windows 设备）。精确结论只可能来自**已探测过**的设备：
      // SPEC §7.2-#2 禁止在渲染列表时主动探测（会变成 N 次探测）→ 结论取自 GET /api/devices 的
      // probe 缓存（批次 AK① 起该缓存带 runtimes 原文，同 probe 端点结构）。
      // 缓存三态别混（见 stores/deviceContext.ts 的 deviceRuntimes 注释）：
      //   条目 installed=true   → 绿（实测装过）
      //   条目 installed=false  → 红（实测没装）
      //   无该条目 / runtimes 为 null / []  → **无结论，保持中性黄**（未探测过 ≠ 探过没有；
      //     实测不到数据时标红就是用户抱怨的「应该也可执行却标红」）
      // 本机例外：后端已按同判据算好 available（envcheck 的 ALT_RUNTIME_RULES）→ 可用它收紧。
      if (!currentDeviceId) {
        if (s.available === true) return { cls: 'ok', sym: '●', title: `${curDeviceLabel} 上可运行：经 ${alt} 运行` }
        if (s.available === false) return { cls: 'err', sym: '●', title: `${curDeviceLabel} 上不可运行：未检测到 ${alt}（或运行时不满足要求）` }
      } else {
        const rt = deviceRuntimes[currentDeviceId]?.find((r) => r.name === alt)
        if (rt) {
          return rt.installed
            ? { cls: 'ok', sym: '●', title: `${curDeviceLabel} 上可运行：设备信息最近一次探测到 ${alt}${rt.version ? `（${rt.version}）` : ''}，${ext} 经其运行` }
            : { cls: 'err', sym: '●', title: `${curDeviceLabel} 上不可运行：设备信息最近一次探测未发现 ${alt}` }
        }
      }
      return {
        cls: 'warn', sym: '●',
        title: `${curDeviceLabel} 上需 ${alt} 才能运行：${ext} 是 Unix 原生脚本，Windows 上由 Git Bash 提供（MSYS2/WSL 亦可）；该设备是否已装需在设备上实测`,
      }
    }
    if (!currentDeviceId) {
      // 本机：平台已匹配，再看后端环境检测（依赖/运行时缺失 → 仍标红）
      if (s.available == null) return { cls: '', sym: '○', title: '本机环境检测未知（检测未返回结果）' }
      if (s.available === false) return { cls: 'err', sym: '●', title: `${curDeviceLabel} 上环境检测未通过（依赖或运行时缺失）` }
      return { cls: 'ok', sym: '●', title: `${curDeviceLabel} 上可运行：环境检测通过` }
    }
    return { cls: 'ok', sym: '●', title: `${curDeviceLabel} 上可运行（平台匹配；依赖/运行时需在设备上实测）` }
  }

  // G2-7 根因修复：「标签」列宽交给 CSS —— 下面的 th 上声明 width:1% + min/max（内容驱动、有上下限）。
  // 旧实现在这里用 scrollWidth 量宽再 setState 回写 th 宽度 —— 而量到的盒子宽度本身由该 state 决定，
  // 内容不溢出时 scrollWidth 返回的是「盒宽」而非内容宽 → 量到上限就永远写回上限，只增不减；
  // 列宽卡在 400 后挤压「最近运行」→ 表头文字换行 → 行高 38→48。
  // 纯 CSS 下移除芯片后列宽自动缩回（table-layout:auto 重算），无 state 可失同步。

  const doRun = async (s: ScriptItem) => {
    setRunning(true)
    try {
      await runScript({ script_id: s.id })
      message.success('已触发执行')
      // 批次 AI ②：照 DesktopRunHistory「再次执行」的写法 —— 新记录立刻进列表（带 running 状态），
      // 轮询才接得上把它追到终态；否则刚触发的脚本在这列永远停在「从未」。
      setReload((t) => t + 1)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '执行失败')
    } finally {
      setRunning(false)
    }
  }

  const onRowContext = (e: React.MouseEvent, s: ScriptItem) => {
    e.preventDefault()
    showContextMenu(e.clientX, e.clientY, [
      { label: '打开工作区', onClick: () => navigate(`/scripts/${s.id}`) },
      { label: '立即执行', onClick: () => doRun(s) },
      {
        label: '导出',
        onClick: async () => {
          const res = await exportScript(s.id)
          await saveBinaryFile(`${s.name}.zip`, res.data)
        },
      },
      {
        label: '删除',
        onClick: () =>
          Modal.confirm({
            title: `删除脚本 ${s.name}？`,
            onOk: async () => {
              await deleteScript(s.id)
              message.success('已删除')
              setSelected(null)
              loadCatalog()
              setReload((v) => v + 1)
            },
          }),
      },
    ])
  }

  const selMeta = selected
  // 右栏「最近运行」的状态符号（表格行用的是行内 st；这里是选中脚本那一份）
  const selSt = statusSymbol(selMeta?.last_run?.status)

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '200px 1fr 280px', flex: 1, minHeight: 0 }}>
      {/* ── 左：目录树 + 标签 ── */}
      <div style={{ background: 'var(--panel)', borderRight: '1px solid var(--border)', padding: '8px 0', fontSize: 13.5, overflow: 'auto' }}>
        <div
          onClick={() => { setDir(''); setPage(1) }}
          style={{
            padding: '5px 14px', cursor: 'pointer',
            color: dir === '' ? 'var(--pri)' : 'var(--muted)',
            background: dir === '' ? 'var(--float)' : 'transparent',
          }}
        >
          全部 <span className="mono" style={{ float: 'right' }}>{catalogTotal}</span>
        </div>
        {tree.map((n) => (
          <div
            key={n.path}
            onClick={() => { setDir(n.path); setPage(1) }}
            style={{
              padding: `5px 14px 5px ${14 + n.depth * 14}px`, cursor: 'pointer',
              color: dir === n.path ? 'var(--pri)' : n.depth === 0 ? 'var(--muted)' : 'var(--text)',
              background: dir === n.path ? 'var(--float)' : 'transparent',
            }}
          >
            {n.hasChild ? (
              <span
                onClick={(e) => { e.stopPropagation(); setExpanded((p) => ({ ...p, [n.path]: p[n.path] === false })) }}
                style={{ color: 'var(--muted)', marginRight: 4 }}
              >
                {expanded[n.path] === false ? '▸' : '▾'}
              </span>
            ) : null}
            {n.name} <span className="mono" style={{ float: 'right' }}>{n.count}</span>
          </div>
        ))}
        <div style={{ padding: '14px 14px 4px', fontSize: 12, color: 'var(--muted)', letterSpacing: '.04em' }}>标签</div>
        <div style={{ padding: '4px 14px', display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {tags.map((t) => (
            <span key={t.id} className="tag" style={{ cursor: 'default' }}>{t.name} {tagCount(t.name)}</span>
          ))}
          {tags.length === 0 && <span className="mono">无标签</span>}
        </div>
      </div>

      {/* ── 中：筛选行 + 列表 ── */}
      {/* 批次 Z（真 bug「脚本库只能看到前 18 条，滚不动」）：本列是 grid 项 + flex column 容器，
          必须显式 minHeight:0。grid/flex 项的 min-height 默认 auto（= 内容最小高）：本列内容
          （38 表头 + 25×37 行 = 1022px）把列撑到 1022，于是里面 :333 那个 flex:1 + overflow:auto
          的表格盒永远拿到「内容高」→ 从不溢出 → 无滚动条；多出的 291px 被 .desktop-mode 根的
          overflow:hidden 裁掉（实测 scrollHeight == clientHeight = 964、scrollTop 恒 0）。
          minHeight:0 让本列收敛回 grid 行高（731），表格盒才真正拿到约束并出现滚动。
          overflow !== visible 的兄弟列（:250 / :418）不受影响，因为规范把它们的 min-height:auto 解析为 0。 */}
      <div style={{ minWidth: 0, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        <div className="filterbar">
          <input
            className="sel"
            style={{ width: 180 }}
            placeholder="搜索…"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1) }}
          />
          <Sel
            width={96}
            value={category}
            onChange={(v) => { setCategory(v); setPage(1) }}
            options={[
              { value: '', label: '全部类型' },
              { value: 'python', label: 'python' },
              { value: 'shell', label: 'shell' },
              { value: 'bat', label: 'bat' },
              { value: 'powershell', label: 'powershell' },
            ]}
          />
          {/* G2-7 / 批次 I-1：标签筛选（多选）回到筛选行，紧跟「全部类型」之后；已选芯片在表头「标签」列名后 */}
          <Sel
            testid="tag-filter-select"
            placeholder="全部标签"
            values={tagFilter.map(String)}
            onToggle={(v) => { const id = Number(v); setTagFilter((f) => (f.includes(id) ? f.filter((x) => x !== id) : [...f, id])); setPage(1) }}
            options={tags.map((t) => ({ value: String(t.id), label: t.name }))}
          />
          {/* 管理入口（G2-6：高 26 + 顶部与同排 .sel 对齐；批次 M-6：普通控件外观，不再主色高亮）
              —— 底 --panel2 / 边框 --border / 文字 --text，与「全部标签」Sel 关闭态同款 */}
          <span className="tag" style={{ height: 26, padding: '0 10px', fontSize: 13, color: 'var(--text)', cursor: 'pointer' }}
            data-testid="manage-tags" onClick={() => setTagPanelOpen(true)}>管理标签…</span>
          {/* 原型语汇：操作以 .tag 芯片承载（用户拍板保留 上传/导入/扫描） */}
          <span className="tag" style={{ cursor: 'pointer' }} onClick={() => fileRef.current?.click()}>上传</span>
          <span className="tag" style={{ cursor: 'pointer' }} onClick={onImport}>导入</span>
          <span className="tag" style={{ cursor: 'pointer' }} onClick={onScan}>扫描</span>
          <input ref={fileRef} type="file" style={{ display: 'none' }}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) onUpload(f); e.target.value = '' }} />
          <span className="mono" style={{ marginLeft: 'auto' }}>{total} 个脚本</span>
        </div>
        <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
          <table>
            <thead>
              <tr>
                <th style={{ width: 28 }} />
                <th>名称</th>
                {/* G2-7 / 批次 I-1：已选标签芯片就在「标签」列名之后，同一格同一行（✕ 点击移除）；
                    多选下拉已移回筛选行 → 本格内只有芯片（无下拉控件）。 */}
                {/* G2-7 根因修复：列宽 = 内容宽（能增也能减），收敛到 190~400。
                    width:1% 是「收缩包裹」写法：让本列不吸余量（余量仍归「名称」列，
                    0 芯片时恢复到 190/348 的原状），实际宽度由内容的 max-content 决定；
                    min-width 190 兜住 0 芯片态，max-width 400 为上限（超出由 .taghead-x 内部横滚）。
                    盒内不再声明宽度 → 内容宽即列宽，移除芯片后自动缩回。 */}
                <th style={{ width: '1%', minWidth: 190, maxWidth: 400, verticalAlign: 'middle' }}>
                  <div className="taghead-x" style={{ display: 'flex', alignItems: 'center', gap: 6, height: 26, whiteSpace: 'nowrap', overflowX: 'auto', overflowY: 'hidden' }}>
                    <span style={{ flex: 'none' }}>标签</span>
                    {tagFilter.map((id) => {
                      const t = tags.find((x) => x.id === id)
                      return (
                        <span className="tag chip" key={id} data-testid="tag-filter-chip" style={{ flex: 'none' }}>
                          <span className="dotc" style={{ background: t?.color || 'var(--sh-dim)' }} />
                          {t?.name || id}
                          <span style={{ marginLeft: 6, cursor: 'pointer', color: 'var(--muted)' }}
                            data-testid={`tag-filter-remove-${id}`} onClick={() => { setTagFilter((f) => f.filter((x) => x !== id)); setPage(1) }}>✕</span>
                        </span>
                      )
                    })}
                  </div>
                </th>
                <th style={{ width: 110 }}>类型</th>
                <th style={{ width: 120 }}>最近运行</th>
              </tr>
            </thead>
            <tbody>
              {items.map((s) => {
                const st = statusSymbol(s.last_run?.status)
                const av = availOf(s)
                const on = selected?.id === s.id
                return (
                  <tr
                    key={s.id}
                    onClick={() => setSelected(s)}
                    onDoubleClick={() => navigate(`/scripts/${s.id}`)}
                    onContextMenu={(e) => onRowContext(e, s)}
                    style={{ background: on ? 'var(--float)' : undefined, cursor: 'pointer' }}
                  >
                    <td className={av.cls} style={av.sym === '○' ? { color: 'var(--muted)' } : undefined} title={av.title}>
                      {av.sym}
                    </td>
                    <td style={{ color: 'var(--pri)' }}>{s.name}</td>
                    <td data-testid="tagcell">
                      {(s.tags || []).length === 0 ? (
                        <span className="mono" style={{ color: 'var(--sh-dim)' }}>—</span>
                      ) : (
                        <>
                          {(s.tags || []).slice(0, TAG_CELL_MAX).map((name) => (
                            <span className="tag chip" key={name}>
                              <span className="dotc" style={{ background: tagColor(name) }} data-testid="tagcell-dot" />{name}
                            </span>
                          ))}
                          {(s.tags || []).length > TAG_CELL_MAX && (
                            <span className="tag chip" style={{ cursor: 'pointer' }}
                              data-testid="tagcell-more"
                              title={(s.tags || []).join(' · ')}
                              onClick={(e) => { e.stopPropagation(); setTagPanelOpen(true) }}>
                              +{(s.tags || []).length - TAG_CELL_MAX}
                            </span>
                          )}
                        </>
                      )}
                    </td>
                    <td><span className="tag">{CATEGORY_LABEL[s.category] || s.category}</span></td>
                    <td className={`mono ${st.cls}`}>{st.sym ? `${st.sym} ` : ''}{timeAgo(s.last_run?.started_at)}</td>
                  </tr>
                )
              })}
              {!loading && items.length === 0 && (
                <tr><td colSpan={5} style={{ textAlign: 'center', color: 'var(--muted)', padding: '28px 0' }}>无匹配脚本</td></tr>
              )}
            </tbody>
          </table>
        </div>
        {/* 批次 AE：分页控件 —— 与运行历史（DesktopRunHistory §3）逐项同款，不另造一套 */}
        <div style={{ padding: '8px 14px', display: 'flex', justifyContent: 'flex-end', gap: 10, color: 'var(--muted)', fontSize: 13 }}>
          <span className="mono" style={{ cursor: page > 1 ? 'pointer' : 'default' }} onClick={() => page > 1 && setPage(page - 1)}>‹</span>
          <span className="mono" style={{ color: 'var(--text)' }}>{page} / {pages}</span>
          <span className="mono" style={{ cursor: page < pages ? 'pointer' : 'default' }} onClick={() => page < pages && setPage(page + 1)}>›</span>
        </div>
      </div>

      {/* ── 右：摘要卡 ── */}
      <div style={{ background: 'var(--panel)', borderLeft: '1px solid var(--border)', padding: 14, overflow: 'auto' }}>
        {!selMeta ? (
          <span className="mono">单击列表选中脚本</span>
        ) : (
          <>
            <b style={{ fontSize: 14.5 }}>{selMeta.name}</b>
            <div style={{ margin: '6px 0 10px', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              <span className="tag">{CATEGORY_LABEL[selMeta.category] || selMeta.category}</span>
              {(selMeta.tags || []).map((t) => <span key={t} className="tag">{t}</span>)}
            </div>
            <div className="kv"><span>目录</span><span>{selMeta.relative_path || '/'}</span></div>
            <div className="kv">
              <span>最近运行</span>
              <span className={selSt.cls}>
                {selSt.sym ? `${selSt.sym} ` : ''}{timeAgo(selMeta.last_run?.started_at)}
                {selMeta.last_run?.duration != null ? ` · ${durationText(selMeta.last_run.duration)}` : ''}
              </span>
            </div>
            <div className="kv"><span>定时任务</span><span>{sched.total} 个{sched.enabled ? ` · ${sched.enabled} 启用` : ''}</span></div>
            <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
              <span className="btn pri" style={{ flex: 1, textAlign: 'center', cursor: 'pointer' }} onClick={() => doRun(selMeta)}>
                {running ? '执行中…' : '▷ 执行'}
              </span>
              <span className="btn" style={{ cursor: 'pointer' }} onClick={() => navigate(`/schedules?script_id=${selMeta.id}`)}>定时</span>
              <span
                className="btn"
                style={{ cursor: 'pointer' }}
                onClick={async () => {
          const res = await exportScript(selMeta.id)
          await saveBinaryFile(`${selMeta.name}.zip`, res.data as unknown as Uint8Array)
        }}
              >
                导出
              </span>
            </div>
            <div style={{ marginTop: 14, fontSize: 12, color: 'var(--muted)', letterSpacing: '.04em' }}>最近输出</div>
            <div
              className="out"
              style={{ marginTop: 6, padding: 8, fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-all', maxHeight: 220 }}
            >
              {output || <span className="mono">（无输出记录）</span>}
            </div>
          </>
        )}
      </div>

      {/* v2.16 §6.7：标签管理浮层（560px；入口 = 筛选行「管理标签…」/ 标签列 +N） */}
      <TagManagerPalette open={tagPanelOpen} onClose={() => setTagPanelOpen(false)} />
    </div>
  )
}
