/**
 * V5-G 桌面形态 · §2.1 定时任务列表 / §2.2 周历（1:1 复刻 mockup §2.1/§2.2）
 * 页头：标题 + 计数 + .seg（列表/周历）+ 主按钮「+ 新建任务」
 * 筛选行：4 个 .search 形态控件（名称 180 / 设备 130 / 执行位置 130 / 状态 110）
 * 表格 9 列：状态点 | 名称 | 脚本 | 设备 | 触发 | 执行位置 | 上次结果 | 启用(.switch) | 操作(tag)
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Modal, Form, Input, Select, Switch, message } from 'antd'
import ScheduleWeekView, { weekStats } from './ScheduleWeekView'
import Sel from './Sel'
import {
  getSchedules, createSchedule, updateSchedule, deleteSchedule, runScheduleNow, getScripts, getDevices,
} from '../../services/api'
import type { ScheduleItem } from '../../services/api'
import { durationText } from '../../utils/format'

function triggerText(s: ScheduleItem) {
  if (s.cron_expr) return s.cron_expr
  const n = s.interval_seconds || 0
  if (n % 3600 === 0) return `每 ${n / 3600} 小时`
  if (n % 60 === 0) return `每 ${n / 60} 分钟`
  return `每 ${n} 秒`
}

function lastText(s: ScheduleItem) {
  const lr = s.last_run
  if (!lr || !lr.started_at) return { cls: 'mono', txt: '从未运行', muted: true }
  const t = new Date(lr.started_at)
  const hm = `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`
  if (lr.status === 'timeout') return { cls: 'warn', txt: '⏱ 上次超时', muted: false }
  if (lr.status === 'success') return { cls: 'ok', txt: `${hm} · ${durationText(lr.duration)}`, muted: false }
  return { cls: 'err', txt: `${hm} · ${lr.exit_code != null ? `exit ${lr.exit_code}` : '失败'}`, muted: false }
}

function dotColor(s: ScheduleItem) {
  if (!s.enabled) return 'var(--sh-dim, #4a4d56)'   // 原型 §2.1 第 730 行「从未运行/停用」点 = #4a4d56（v2.9 曾误判为跑题色 → v2.11 回滚）
  const st = s.last_run?.status
  if (st === 'success') return 'var(--ok)'
  if (st === 'failed') return 'var(--err)'
  if (st === 'timeout') return 'var(--warn)'
  return 'var(--ok)'
}

export default function DesktopSchedules() {
  const [items, setItems] = useState<ScheduleItem[]>([])
  const [scripts, setScripts] = useState<{ id: number; name: string }[]>([])
  const [devices, setDevices] = useState<{ id: number; name: string }[]>([])
  const [view, setView] = useState<'list' | 'week'>('list')
  const [q, setQ] = useState('')
  const [fDev, setFDev] = useState('')
  const [fLoc, setFLoc] = useState('')
  const [fSt, setFSt] = useState('')
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<ScheduleItem | null>(null)
  const navigate = useNavigate()
  const [form] = Form.useForm()
  const trigType = Form.useWatch('trig_type', form)

  const load = () => {
    getSchedules().then((r) => setItems(r.data.items || [])).catch(() => {})
    getDevices().then((r) => setDevices(r.data || [])).catch(() => {})
  }
  // 批次 BD：F5 刷新（scripthub:refresh）与挂载取数同路
  useEffect(() => {
    load()
    window.addEventListener('scripthub:refresh', load)
    return () => window.removeEventListener('scripthub:refresh', load)
  }, [])
  useEffect(() => { getScripts({ page: 1, page_size: 100 }).then((r) => setScripts(r.data.items || [])).catch(() => {}) }, [])

  const rows = useMemo(() => items.filter((s) => {
    if (q && !`${s.name} ${s.script_name || ''}`.toLowerCase().includes(q.toLowerCase())) return false
    if (fDev && String(s.device_id || 'local') !== fDev) return false
    if (fLoc && s.exec_location !== fLoc) return false
    if (fSt === 'on' && !s.enabled) return false
    if (fSt === 'off' && s.enabled) return false
    return true
  }), [items, q, fDev, fLoc, fSt])

  const on = items.filter((s) => s.enabled).length
  const off = items.length - on
  // 与周历的周统计卡同源（原独立估算会让两处数字打架）
  const { triggers: weekRuns, failed: weekFailed } = weekStats(items)

  const toggle = async (s: ScheduleItem) => {
    await updateSchedule(s.id, { enabled: !s.enabled } as Partial<ScheduleItem>)
    load()
  }
  const submit = async () => {
    const v: any = await form.validateFields()
    const payload: Partial<ScheduleItem> = {
      name: v.name, script_id: v.script_id, device_id: v.device_id || null,
      exec_location: v.exec_location || 'local', timeout: v.timeout || 0,
      enabled: v.enabled ?? true,
      cron_expr: v.trig_type === 'cron' ? v.cron_expr : null,
      interval_seconds: v.trig_type === 'interval' ? v.interval_seconds : null,
    }
    if (editing) { await updateSchedule(editing.id, payload); message.success('已保存') }
    else { await createSchedule(payload); message.success('已创建') }
    setOpen(false); setEditing(null); form.resetFields(); load()
  }

  return (
    <>
      <div className="pagehead">
        <b>定时任务</b>
        <span className="cnt">{view === 'week' ? `本周 ${weekRuns} 次触发 · ${weekFailed} 失败` : `${on} 启用 · ${off} 停用 · 本周 ${weekRuns} 次触发`}</span>
        <span className="seg" style={{ marginLeft: 'auto' }}>
          <span className={`sg${view === 'list' ? ' on' : ''}`} onClick={() => setView('list')} style={{ cursor: 'pointer' }}>列表</span>
          <span className={`sg${view === 'week' ? ' on' : ''}`} onClick={() => setView('week')} style={{ cursor: 'pointer' }}>周历</span>
        </span>
        <span className="btn pri" style={{ cursor: 'pointer' }}
          onClick={() => { setEditing(null); form.resetFields(); setOpen(true) }}>+ 新建任务</span>
      </div>

      {view === 'list' ? (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '0 18px 12px' }}>
            <input className="search" style={{ width: 180 }} placeholder="🔍 按名称/脚本…" value={q} onChange={(e) => setQ(e.target.value)} />
            <Sel width={130} value={fDev} onChange={setFDev}
              options={[{ value: '', label: '设备：全部' }, { value: 'local', label: '本机' },
                ...devices.map((d) => ({ value: String(d.id), label: d.name }))]} />
            <Sel width={130} value={fLoc} onChange={setFLoc}
              options={[{ value: '', label: '执行位置：全部' }, { value: 'local', label: '本机调度' }, { value: 'device', label: '下放设备' }]} />
            <Sel width={110} value={fSt} onChange={setFSt}
              options={[{ value: '', label: '状态：全部' }, { value: 'on', label: '已启用' }, { value: 'off', label: '已停用' }]} />
            <span className="mono" style={{ marginLeft: 'auto' }}>共 {rows.length} 条</span>
          </div>
          <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
            <table>
              <thead>
                <tr>
                  <th style={{ width: 30 }} /><th>名称</th><th>脚本</th><th style={{ width: 140 }}>设备</th>
                  <th style={{ width: 120 }}>触发</th><th style={{ width: 140 }}>执行位置</th><th style={{ width: 150 }}>上次结果</th>
                  <th style={{ width: 60 }}>启用</th><th style={{ width: 210 }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => {
                  const lt = lastText(s)
                  return (
                    <tr key={s.id}>
                      <td><span className="dot" style={{ background: dotColor(s) }} /></td>
                      <td style={s.enabled ? undefined : { color: 'var(--muted)' }}>{s.name}</td>
                      <td className="mono">{s.script_name || `#${s.script_id}`}</td>
                      <td>{s.device_name || '本机'}</td>
                      <td className="mono">{triggerText(s)}</td>
                      <td>
                        <span className="tag">
                          {s.exec_location === 'device'
                            ? `下放·${s.device_type === 'windows' ? 'schtasks' : 'crontab'} ⇗`
                            : '本机调度'}
                        </span>
                      </td>
                      <td className={lt.muted ? 'mono' : lt.cls} style={lt.muted ? { color: 'var(--muted)' } : undefined}>{lt.txt}</td>
                      <td>
                        <span className={`switch${s.enabled ? '' : ' off'}`} role="switch" aria-checked={s.enabled}
                          style={{ cursor: 'pointer' }} onClick={() => toggle(s)} />
                      </td>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        <span className="tag" style={{ cursor: 'pointer' }}
                          onClick={() => runScheduleNow(s.id).then(() => message.success('已触发')).catch(() => message.error('触发失败'))}>立即运行</span>{' '}
                        <span className="tag" style={{ cursor: 'pointer' }}
                          onClick={() => { setEditing(s); form.setFieldsValue({ ...s, trig_type: s.cron_expr ? 'cron' : 'interval' }); setOpen(true) }}>编辑</span>{' '}
                        <span className="tag" style={{ cursor: 'pointer' }}
                          onClick={() => navigate(`/history?schedule_id=${s.id}`)}>历史</span>{' '}
                        <span className="tag danger" style={{ cursor: 'pointer' }}
                          onClick={() => Modal.confirm({ title: `删除任务 ${s.name}？`, content: '若已下放到目标设备，将同时清理远端标记块。', onOk: async () => { await deleteSchedule(s.id); load() } })}>删除</span>
                      </td>
                    </tr>
                  )
                })}
                {rows.length === 0 && (
                  <tr><td colSpan={9} style={{ textAlign: 'center', color: 'var(--muted)', padding: '28px 0' }}>暂无定时任务</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <div style={{ padding: '8px 14px', display: 'flex', justifyContent: 'flex-end' }}>
            <span className="mono">共 {rows.length} 条</span>
          </div>
        </>
      ) : (
        <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
          <ScheduleWeekView
            items={items.filter((s) => s.enabled)}
            onEdit={(s) => { setEditing(s); setOpen(true) }}
            onViewRuns={(s) => navigate(`/history?schedule_id=${s.id}`)}
          />
        </div>
      )}

      <Modal title={editing ? '编辑任务' : '新建任务'} open={open} onCancel={() => setOpen(false)} onOk={submit} width={480} style={{ top: 60 }}>
        <Form form={form} layout="vertical" style={{ marginTop: 12 }} initialValues={{ trig_type: 'cron', exec_location: 'local', timeout: 0, enabled: true }}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="script_id" label="脚本" rules={[{ required: true }]}>
            <Select options={scripts.map((s) => ({ value: s.id, label: s.name }))} showSearch optionFilterProp="label" />
          </Form.Item>
          <Form.Item name="device_id" label="目标设备">
            <Select allowClear placeholder="本机" options={devices.map((d) => ({ value: d.id, label: d.name }))} />
          </Form.Item>
          <Form.Item name="exec_location" label="执行位置" extra="下放后任务写入目标设备系统调度器，工具完全退出也照跑">
            <Select options={[{ value: 'local', label: '本机调度' }, { value: 'device', label: '下放设备' }]} />
          </Form.Item>
          <Form.Item name="trig_type" label="触发类型">
            <Select options={[{ value: 'cron', label: 'Cron 表达式' }, { value: 'interval', label: '固定间隔' }]} />
          </Form.Item>
          {trigType === 'interval'
            ? <Form.Item name="interval_seconds" label="间隔（秒）" rules={[{ required: true }]}><Input /></Form.Item>
            : <Form.Item name="cron_expr" label="Cron 表达式" rules={[{ required: true }]}><Input placeholder="*/30 * * * *" /></Form.Item>}
          <Form.Item name="timeout" label="超时（秒，0=不限）"><Input /></Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>
    </>
  )
}
