/**
 * V5-G 桌面形态 · §3 运行历史（1:1 复刻 mockup §3）
 * 结构：左 1fr（筛选行 + 七列表格 + 分页）+ 右 360px 输出抽屉（选中行内容）
 * 状态只走语义色（勾叉已移除）；耗时/时间等宽。
 */
import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { getRunHistory, getSchedules } from '../../services/api'
import type { RunHistoryItem } from '../../services/api'
import { durationText } from '../../utils/format'
import { useDeviceContext } from '../../stores/deviceContext'
import Sel from './Sel'

const SYM: Record<string, { s: string; c: string }> = {
  success: { s: '', c: 'ok' },
  failed: { s: '', c: 'err' },
  timeout: { s: '⏱', c: 'warn' },
  running: { s: '▶', c: 'warn' },
  killed: { s: '■', c: 'err' },
}

function clock(iso: string) {
  const d = new Date(iso)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}

export default function DesktopRunHistory() {
  const [items, setItems] = useState<RunHistoryItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('')
  const [source, setSource] = useState('')
  // 设备筛选：'local'=本机（不传 device_id），数字串=该设备；默认跟随当前设备上下文
  const { currentDeviceId, deviceNames, refreshDevices } = useDeviceContext()
  const [fDevice, setFDevice] = useState<string>(currentDeviceId ? String(currentDeviceId) : 'local')
  // §2.3 / §2.1「历史」跳转带 script_id / schedule_id → 预置筛选（一次性读 URL，不引入路由依赖）
  const [scriptId, setScriptId] = useState(() => new URLSearchParams(window.location.search).get('script_id') || '')
  const [scheduleId, setScheduleId] = useState(() => new URLSearchParams(window.location.search).get('schedule_id') || '')
  const [scripts, setScripts] = useState<{ id: number; name: string }[]>([])
  const [sel, setSel] = useState<RunHistoryItem | null>(null)
  const navigate = useNavigate()
  const hasFilter = !!(scriptId || scheduleId || status || source || fDevice !== 'local')
  const pageSize = 20

  const load = useCallback(() => {
    return getRunHistory({
      page, page_size: pageSize,
      status: status || undefined,
      source: source || undefined,
      script_id: scriptId ? Number(scriptId) : undefined,
      schedule_id: scheduleId ? Number(scheduleId) : undefined,
      // 'local'=本机（本机记录 device_id 为空，后端 == 过滤不能命中 NULL，故不传）
      device_id: fDevice !== 'local' && fDevice !== '' ? Number(fDevice) : undefined,
    } as any)
      .then((res) => { setItems(res.data.items || []); setTotal(res.data.total || 0) })
      .catch(() => {})
  }, [page, status, source, scriptId, scheduleId, fDevice])

  // 批次 BD：F5 刷新（scripthub:refresh）与挂载取数同路；load 是既有实现，不另写一份
  useEffect(() => {
    load()
    window.addEventListener('scripthub:refresh', load)
    return () => window.removeEventListener('scripthub:refresh', load)
  }, [load])

  // 批次 AD ①：有 running 记录时轮询重拉。后端在**执行结束时**（不依赖任何前端连接）就已把
  // status/finished_at/duration 落库 —— 缺的只是本页不会自行重拉，于是记录一直停在「运行中」，
  // 得切页/下次执行才变。全部进终态立刻停表：没有运行时不留着轮询。
  const hasRunning = items.some((i) => i.status === 'running')
  useEffect(() => {
    if (!hasRunning) return
    const timer = setInterval(load, 2500)
    return () => clearInterval(timer)
  }, [hasRunning, load])

  useEffect(() => {
    refreshDevices()
  }, [])

  // G2-8：从调度页跳转（?schedule_id=N）→ 设备筛选自动切到该任务的设备（device_id 为空 = 本机）
  useEffect(() => {
    if (!scheduleId) return
    getSchedules()
      .then((res) => {
        const s = (res.data.items || []).find((x) => x.id === Number(scheduleId))
        if (s) setFDevice(s.device_id ? String(s.device_id) : 'local')
      })
      .catch(() => {})
  }, [scheduleId])

  useEffect(() => {
    import('../../services/api').then(({ getScripts }) =>
      getScripts({ page: 1, page_size: 100 }).then((r) => setScripts(r.data.items || [])).catch(() => {})
    )
  }, [])

  const today = items.filter((i) => new Date(i.started_at).toDateString() === new Date().toDateString()).length
  const rows = items   // v2.7 起筛选全部由后端作用于行数据，此处不再重复过滤（会与分页打架）
  const pages = Math.max(1, Math.ceil(total / pageSize))
  // 抽屉读的是同一个数据源（items 的当前行，不是点击时的快照）：轮询拿到新状态后抽屉跟着变，
  // 否则运行中那条点开后详情永远停在「运行中/无输出」。行不在当前页时回落快照。
  const selRow = sel ? items.find((i) => i.id === sel.id) || sel : null

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 360px', flex: 1, minHeight: 0 }}>
      {/* 批次 Z：与脚本库中栏同一根因（grid 项 + flex column 容器缺 minHeight:0 → 内容撑高后
          :102 那个 flex:1 + overflow:auto 的表格盒永远不溢出 → 首页 20 行时滚不动、末尾几行被
          overflow:hidden 裁掉）。显式 minHeight:0 收敛回网格行高。 */}
      <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0 }}>
        <div className="filterbar">
          <Sel width={170} value={scriptId} onChange={(v) => { setScriptId(v); setPage(1) }}
            options={[{ value: '', label: '按脚本筛选' }, ...scripts.map((s) => ({ value: String(s.id), label: s.name }))]} />
          <Sel width={100} value={status} onChange={(v) => { setStatus(v); setPage(1) }}
            options={[{ value: '', label: '状态：全部' }, { value: 'success', label: '成功' }, { value: 'failed', label: '失败' },
              { value: 'timeout', label: '超时' }, { value: 'running', label: '运行中' }, { value: 'killed', label: '已终止' }]} />
          <Sel width={100} value={source} onChange={setSource}
            options={[{ value: '', label: '来源：全部' }, { value: 'manual', label: '手动' }, { value: 'sched', label: '定时' }]} />
          <Sel width={113} value={fDevice} onChange={(v) => { setFDevice(v); setPage(1) }}
            options={[{ value: 'local', label: '设备：本机' }, ...Object.entries(deviceNames).map(([id, name]) => ({ value: id, label: name }))]} />
          {scheduleId && (
            <span className="tag" style={{ cursor: 'pointer' }} title="点击清除该筛选"
              onClick={() => { setScheduleId(''); setPage(1) }}>定时任务 #{scheduleId} ✕</span>
          )}
          <span className="mono" style={{ marginLeft: 'auto' }}>共 {total} 条 · 今日 {today}</span>
        </div>
        <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
          {rows.length === 0 ? (
            /* 原型 §3.1：空态/筛选无结果给整块居中指引，不白屏（原型只画了「无记录」态，
               筛选无结果沿用同版式但换文案 + 「清除筛选」，避免让用户去脚本库白跑一趟） */
            <div style={{ minHeight: 380, height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 10 }}>
              <div style={{ fontSize: 26, color: 'var(--muted)' }}>🕘</div>
              <div style={{ color: 'var(--text)' }}>{hasFilter ? '未找到匹配记录' : '暂无运行记录'}</div>
              <div style={{ color: 'var(--muted)', fontSize: 13 }}>{hasFilter ? '换个筛选条件，或清除筛选查看全部' : '到「脚本库」选择脚本并执行，或检查设备连接'}</div>
              {hasFilter
                ? <span className="btn" style={{ cursor: 'pointer' }} onClick={() => { setScriptId(''); setScheduleId(''); setStatus(''); setSource(''); setPage(1) }}>清除筛选</span>
                : <span className="btn pri" style={{ cursor: 'pointer' }} onClick={() => navigate('/')}>去脚本库</span>}
            </div>
          ) : (
          <table>
            <thead>
              <tr>
                <th style={{ width: 30 }} />
                <th>脚本</th>
                <th>设备</th>
                <th style={{ width: 80 }}>来源</th>
                <th style={{ width: 90 }}>状态</th>
                <th style={{ width: 80 }}>耗时</th>
                <th style={{ width: 100 }}>开始时间</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const sy = SYM[r.status] || { s: '', c: '' }
                return (
                  <tr key={r.id} onClick={() => setSel(r)} style={{ cursor: 'pointer', background: sel?.id === r.id ? 'var(--float)' : undefined }}>
                    <td className={sy.c}>{sy.s}</td>
                    <td style={{ color: 'var(--pri)' }}>{r.script_name || `#${r.script_id}`}</td>
                    <td>{r.device_name || '本机'}</td>
                    <td><span className="tag">{r.is_scheduled ? '定时' : '手动'}</span></td>
                    <td className={r.status === 'success' ? 'ok' : r.status === 'timeout' ? 'warn' : 'err'}>
                      {r.status === 'timeout' ? '超时' : r.exit_code == null ? r.status : `exit ${r.exit_code}`}
                    </td>
                    <td className="mono">{durationText(r.duration)}</td>
                    <td className="mono">{clock(r.started_at)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          )}
        </div>
        <div style={{ padding: '8px 14px', display: 'flex', justifyContent: 'flex-end', gap: 10, color: 'var(--muted)', fontSize: 13 }}>
          <span className="mono" style={{ cursor: page > 1 ? 'pointer' : 'default' }} onClick={() => page > 1 && setPage(page - 1)}>‹</span>
          <span className="mono" style={{ color: 'var(--text)' }}>{page} / {pages}</span>
          <span className="mono" style={{ cursor: page < pages ? 'pointer' : 'default' }} onClick={() => page < pages && setPage(page + 1)}>›</span>
        </div>
      </div>

      {/* 右：输出抽屉 */}
      <div style={{ background: 'var(--panel)', borderLeft: '1px solid var(--border)', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        {!selRow ? (
          <div style={{ padding: 14 }}><span className="mono">单击左侧记录查看输出</span></div>
        ) : (
          <>
            <div className="dh" style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', borderBottom: '1px solid var(--border)' }}>
              <b style={{ fontSize: 14 }}>运行 #{selRow.id}</b>
              <span className="mono" style={{ marginLeft: 'auto', cursor: 'pointer' }} onClick={() => setSel(null)}>✕</span>
            </div>
            <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)' }}>
              <div style={{ background: 'var(--term)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 10px', fontFamily: 'var(--mono)', fontSize: 12.5, color: 'var(--text)', wordBreak: 'break-all' }}>
                $ {selRow.command}
              </div>
              <div className="kv" style={{ marginTop: 8 }}><span>来源</span><span>{selRow.is_scheduled ? '定时' : '手动'}{selRow.schedule_id ? ` #${selRow.schedule_id}` : ''}</span></div>
              <div className="kv"><span>开始</span><span>{new Date(selRow.started_at).toLocaleString('zh-CN', { hour12: false })}</span></div>
              <div className="kv"><span>耗时</span><span className="mono">{durationText(selRow.duration)}</span></div>
              <div className="kv"><span>退出码</span><span className={selRow.exit_code === 0 ? 'ok' : 'err'}>{selRow.exit_code ?? '—'}</span></div>
            </div>
            <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: '10px 14px' }}>
              <div style={{ background: 'var(--term)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 10px', fontFamily: 'var(--mono)', fontSize: 12.5, color: '#a8b3c2', whiteSpace: 'pre-wrap', wordBreak: 'break-all', minHeight: '100%' }}>
                {selRow.output || '（无输出）'}
              </div>
            </div>
            <div style={{ padding: '10px 14px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <span className="btn" style={{ flex: 1, minWidth: 96, textAlign: 'center', cursor: 'pointer' }}
                onClick={() => navigator.clipboard?.writeText(selRow.output || '')}>复制输出</span>
              <span className="btn pri" style={{ flex: 1, minWidth: 96, textAlign: 'center', cursor: 'pointer' }}
                onClick={async () => {
                  const { runScript } = await import('../../services/api')
                  await runScript({ script_id: selRow.script_id, parameters: JSON.parse(selRow.parameters || '{}') } as any)
                  load()   // 新记录立刻进列表（它带着 running 状态 → 轮询会接着把它追到终态）
                }}>▷ 再次执行</span>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
