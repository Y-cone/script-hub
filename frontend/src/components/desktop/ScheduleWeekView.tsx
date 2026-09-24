/**
 * V5-G（SPEC §2.7 / 原型 §2.2+§2.5）：定时调度周历视图。
 * 结构 = 工具栏（‹ 今天 › + 周区间 + 图例）+ .cal（.cal-head 7 日头 + .cal-row 7 格 + 70px .wk）。
 * 事件条 .ev：.st 状态点 + .t 等宽时间 + .n 任务名；下放任务用 .ev.remote（CSS 出 ⇗），不再手写尾缀。
 * 停用任务不进周历；单日 >4 条折叠（前 3 + 「+N 更多」，.more 切换）；今天 .today；空日 .cell.empty。
 * hover 300ms 详情浮层（§2.3）：格内绝对定位 .pop（原型），非 antd Tooltip。
 */
import { useMemo, useRef, useState } from 'react'
import type { ScheduleItem } from '../../services/api'

const WEEK_DAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const DOT_VAR: Record<string, string> = {
  success: 'var(--ok)', failed: 'var(--err)', timeout: 'var(--warn)', running: 'var(--pri)', none: 'var(--muted)',
}

const pad2 = (n: number) => String(n).padStart(2, '0')
const mmdd = (d: Date) => `${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`

/** 周起始（周一 00:00）；offset=0 即本周，±1 上下周 */
export function weekStartOf(offset: number) {
  const d = new Date()
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7) + offset * 7)
  d.setHours(0, 0, 0, 0)
  return d
}

const MAX_PER_DAY = 28

/** cron 单字段展开：支持 `*` / `*\/N` / `a-b` / `a,b,c` 混合。
    （`*\/10` 这类步长以前被 Number() 吃成 NaN→0，导致每天 6 次的 cron 在周历里只画 1 条、
      页头触发数也只有 7 —— 已修。） */
function expandField(field: string, max: number): number[] {
  if (field === '*') return Array.from({ length: max }, (_, i) => i)
  const out = new Set<number>()
  for (const part of field.split(',')) {
    const step = part.match(/^[*][/]([0-9]+)$/)
    if (step) {
      const n = Number(step[1]) || 1
      for (let v = 0; v < max; v += n) out.add(v)
      continue
    }
    const range = part.match(/^([0-9]+)-([0-9]+)$/)
    if (range) {
      for (let v = Number(range[1]); v <= Number(range[2]); v++) if (v < max) out.add(v)
      continue
    }
    const n = Number(part)
    if (!Number.isNaN(n) && n < max) out.add(n)
  }
  return [...out].sort((a, b) => a - b)
}

/** 触发时刻 → 本周期内事件（来自 cron/interval 的触发时间） */
function eventsOf(s: ScheduleItem): { day: number; time: string }[] {
  const out: { day: number; time: string }[] = []
  const countOf = (day: number) => out.reduce((n, e) => (e.day === day ? n + 1 : n), 0)
  const push = (day: number, h: number, mi: number) => {
    // ponytail: 单日封顶 MAX_PER_DAY —— 格子里本来就只露 3 条（折叠），继续渲染只会撑爆格子
    if (countOf(day) >= MAX_PER_DAY) return
    out.push({ day, time: `${pad2(h)}:${pad2(mi)}` })
  }
  if (s.cron_expr) {
    // 支持 `M H * * *`（每天）/ `M H * * D`（周 D），且 M/H 可为 `*\/N`、`a-b`、`a,b`
    const parts = s.cron_expr.trim().split(' ').filter(Boolean)
    if (parts.length === 5) {
      const [mi, h, , , dow] = parts
      const days = dow === '*' ? [0, 1, 2, 3, 4, 5, 6] : expandField(dow, 7).map((d) => (d === 7 ? 0 : d))
      const minutes = expandField(mi, 60)
      const hours = expandField(h, 24)
      for (let day = 0; day < 7; day++) {
        // 原型周日历：列 0=周一。cron dow 0/7=周日、1=周一
        const cronDow = day === 6 ? 0 : day + 1
        if (!days.includes(cronDow)) continue
        for (const hh of hours) for (const mm of minutes) push(day, hh, mm)
      }
    }
  } else if (s.interval_seconds) {
    // interval：按间隔落位（展示用近似——每天按 86400/interval 摊，单日封顶 MAX_PER_DAY）
    const perDay = Math.min(MAX_PER_DAY, Math.max(1, Math.floor(86400 / s.interval_seconds)))
    for (let day = 0; day < 7; day++) {
      for (let k = 0; k < perDay; k++) {
        const t = k * s.interval_seconds
        push(day, Math.floor(t / 3600) % 24, Math.floor(t / 60) % 60)
      }
    }
  }
  return out
}

/**
 * 一周触发/失败统计——页头 cnt 与周统计卡同源，避免两处数字打架。
 * ponytail: cron 每周重复、interval 只按每天落位近似，各周统计值相同，故无 offset 参数；接 runs 表后按周内实际运行统计
 */
export function weekStats(items: ScheduleItem[]) {
  let triggers = 0
  let failed = 0
  for (const s of items) {
    if (!s.enabled) continue
    const n = eventsOf(s).length
    triggers += n
    // ponytail: 只有「最近一次运行」状态，失败按整周同状态计；接 runs 表后按周内实际失败数统计
    if (s.last_run?.status === 'failed') failed += n
  }
  return { triggers, failed }
}

type Hover = { day: number; top: number; time: string; s: ScheduleItem }

/** §2.3「本次触发」行：状态字形 + 本格触发时刻 + exit 码 */
function runLabel(h: Hover) {
  const st = h.s.last_run?.status || 'none'
  if (st === 'none') return '— 从未运行'
  if (st === 'running') return `▶ ${h.time} · exit ${h.s.last_run?.exit_code ?? '-'}`
  return `${h.time} · exit ${h.s.last_run?.exit_code ?? '-'}`
}

export default function ScheduleWeekView({ items, onEdit, onViewRuns }: {
  items: ScheduleItem[]
  onEdit?: (s: ScheduleItem) => void
  onViewRuns?: (s: ScheduleItem) => void
}) {
  const [offset, setOffset] = useState(0)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  // §2.3 hover 详情：进 300ms 出 320ms；移到浮层内可点按钮（不清则不消失）
  const [hover, setHover] = useState<Hover | null>(null)
  const hoverTimer = useRef<number | undefined>(undefined)
  const cancelHover = () => { if (hoverTimer.current) window.clearTimeout(hoverTimer.current); hoverTimer.current = undefined }
  const enterHover = (h: Hover) => { cancelHover(); hoverTimer.current = window.setTimeout(() => setHover(h), 300) }
  const leaveHover = () => { cancelHover(); hoverTimer.current = window.setTimeout(() => setHover(null), 320) }
  const weekStart = useMemo(() => weekStartOf(offset), [offset])
  const today = new Date()
  const todayIdx = (today.getDay() + 6) % 7
  const isThisWeek = offset === 0

  const enabled = items.filter((s) => s.enabled) // 停用不进周历（SPEC §2.7）
  const byDay: { s: ScheduleItem; time: string }[][] = [[], [], [], [], [], [], []]
  for (const s of enabled) {
    for (const ev of eventsOf(s)) byDay[ev.day].push({ s, time: ev.time })
  }
  for (const list of byDay) list.sort((a, b) => a.time.localeCompare(b.time))

  const { triggers, failed } = weekStats(items)
  const weekEnd = new Date(weekStart)
  weekEnd.setDate(weekStart.getDate() + 6)

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '0 18px 12px' }}>
        <span className="btn" style={{ cursor: 'pointer' }} title="上一周" onClick={() => setOffset((o) => o - 1)}>‹</span>
        <span className="btn" style={{ cursor: 'pointer', ...(isThisWeek ? { borderColor: 'var(--pri)', color: 'var(--pri)' } : {}) }}
          onClick={() => setOffset(0)}>今天</span>
        <span className="btn" style={{ cursor: 'pointer' }} title="下一周" onClick={() => setOffset((o) => o + 1)}>›</span>
        <b style={{ fontSize: 14 }}>{weekStart.getFullYear()}-{mmdd(weekStart)} ~ {mmdd(weekEnd)}</b>
        <span className="mono">{isThisWeek ? '（本周）' : ''}</span>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 14, fontSize: 12.5, color: 'var(--muted)', alignItems: 'center' }}>
          <span><span className="dot" style={{ background: DOT_VAR.success, marginRight: 4 }} />上次成功</span>
          <span><span className="dot" style={{ background: DOT_VAR.failed, marginRight: 4 }} />上次失败</span>
          <span><span className="mono">⇗</span> 下放任务</span>
        </span>
      </div>
      <div className="cal">
        <div className="cal-head">
          {WEEK_DAYS.map((d, i) => (
            <div key={d} className={`d${isThisWeek && i === todayIdx ? ' today' : ''}`}>
              <b>{d}</b>
              <span className="mono">{mmdd(new Date(weekStart.getFullYear(), weekStart.getMonth(), weekStart.getDate() + i))}</span>
            </div>
          ))}
          <div>周统计</div>
        </div>
        <div className="cal-row">
          {byDay.map((list, day) => {
            const isToday = isThisWeek && day === todayIdx
            const isExpanded = expanded.has(day)
            const shown = isExpanded || list.length <= 4 ? list : list.slice(0, 3)
            const hidden = list.length - shown.length
            return (
              <div key={day} style={{ position: 'relative' }}
                className={`cell${isToday ? ' today' : ''}${!list.length && !isToday ? ' empty' : ''}`}>
                {list.map(({ s, time }, i) => {
                  const status = s.last_run?.status || 'none'
                  return (
                    <div key={`${s.id}-${i}`}
                      className={`ev${s.exec_location === 'device' ? ' remote' : ''}${i >= shown.length ? ' hidden' : ''}`}
                      onMouseEnter={(e) => enterHover({ day, top: e.currentTarget.offsetTop + 21, time, s })}
                      onMouseLeave={() => leaveHover()}>
                      <span className="st" style={{ background: DOT_VAR[status] || DOT_VAR.none }} />
                      <span className="t">{time}</span>
                      <span className="n">{s.name}</span>
                    </div>
                  )
                })}
                {hover?.day === day && (
                  <div className="pop" style={{ position: 'absolute', left: 8, top: hover.top }}
                    onMouseEnter={cancelHover} onMouseLeave={leaveHover}>
                    <div className="ph">
                      <b style={{ fontSize: 13.5 }}>{hover.s.name}</b>
                      <span className="tag">{hover.s.exec_location === 'device'
                        ? `下放·${hover.s.device_type === 'windows' ? 'schtasks' : 'crontab'} ⇗` : '本机'}</span>
                    </div>
                    <div className="row"><span>脚本</span><span className="mono">{hover.s.script_name || `#${hover.s.script_id}`}</span></div>
                    <div className="row"><span>设备</span><span>{hover.s.device_name || '本机'}</span></div>
                    <div className="row"><span>触发</span><span className="mono">{hover.s.cron_expr || `每 ${hover.s.interval_seconds}s`}</span></div>
                    <div className="row"><span>本次触发</span>
                      <span className={/failed|timeout/.test(hover.s.last_run?.status || '') ? 'err' : ''}>{runLabel(hover)}</span></div>
                    <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                      <span className="btn" style={{ padding: '2px 10px', cursor: 'pointer' }} onClick={() => onViewRuns?.(hover.s)}>查看运行</span>
                      <span className="btn" style={{ padding: '2px 10px', cursor: 'pointer' }} onClick={() => onEdit?.(hover.s)}>编辑</span>
                    </div>
                  </div>
                )}
                {hidden > 0 && (
                  <div className="more" style={{ cursor: 'pointer' }}
                    onClick={() => setExpanded((prev) => { const n = new Set(prev); n.add(day); return n })}>
                    +{hidden} 更多
                  </div>
                )}
                {isExpanded && list.length > 4 && (
                  <div className="more" style={{ cursor: 'pointer' }}
                    onClick={() => setExpanded((prev) => { const n = new Set(prev); n.delete(day); return n })}>
                    收起
                  </div>
                )}
              </div>
            )
          })}
          <div className="wk">
            <div className="big">{triggers}</div>
            <div className="l">次触发 · {failed} 失败</div>
            <div className="bar"><i style={{ width: `${Math.min(100, (triggers / 41) * 100)}%` }} /></div>
          </div>
        </div>
      </div>
    </>
  )
}
