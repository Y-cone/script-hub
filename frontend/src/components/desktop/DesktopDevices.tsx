/**
 * V5-G 桌面形态 · §4 远程设备（1:1 复刻 mockup §4）
 * 页头（N 台 · M 在线）+ 筛选行（名称 / 平台）+ 卡片网格（**本机卡** + 远程卡 + 虚线占位卡）；
 * 测试连接 / 连接就地反馈（无 toast）。
 *
 * 批次 AN（用户拍板）：切换设备的入口从顶栏搬到这里 ——
 *   ① 卡片「连接」= 先探测，**通了才** setCurrentDeviceId（失败只报错、绝不动当前设备）；
 *   ② 「测试连接」保持旧语义（只测不切，顺带回写 latency）；
 *   ③ 本机 = currentDeviceId === null，不是一条 device 记录 → 单独渲染，不进 items；
 *   ④ 筛选维度 = 名称 + 平台（**不做在线状态筛选**：online 是「TTL 内探过且成功」的缓存结论，
 *      拿它筛等于筛「最近谁被点过」；平台是静态属性，永不过期）。
 */
import { useEffect, useState } from 'react'
import { Modal, Form, Input, Select, Spin, message } from 'antd'
import { getDevices, createDevice, updateDevice, deleteDevice, testDevice, probeOnce, getSystemInfo } from '../../services/api'
import type { DeviceItem, SystemInfo } from '../../services/api'
import { useDeviceContext } from '../../stores/deviceContext'
import Sel from './Sel'

/** 就地反馈：kind 决定行标签（连接 / 测试连接）。批次 AO：`connect` 只用于**失败**——
 *  连接成功不再留这一行（用户拍板），只剩「测试连接」的成功提示。 */
type Feedback = { ok: boolean; kind: 'test' | 'connect'; msg: string }

/** 后端 ssh_service.CACHE_TTL = 600s。列表里的 online 是这个窗口内的结论，不是实时状态 —— 文案口径与
 *  DesktopScriptLibrary「设备信息最近一次探测…」保持一致。 */
const TTL = '10 分钟'

/** 平台族：与 stores/deviceContext.devicePlatforms 同一判据（windows → windows，其余 → unix） */
const platformOf = (t: string) => (t === 'windows' ? 'windows' : 'unix')

export default function DesktopDevices() {
  const [items, setItems] = useState<DeviceItem[]>([])
  const [testing, setTesting] = useState<number | null>(null)
  const [connecting, setConnecting] = useState<number | 'local' | null>(null)
  const [result, setResult] = useState<Record<number, Feedback>>({})
  // 批次 AN：筛选（纯前端，GET /api/devices 本就返回全部设备，无需后端参数/分页）
  const [q, setQ] = useState('')
  const [plat, setPlat] = useState('')
  const [local, setLocal] = useState<SystemInfo | null>(null)
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<DeviceItem | null>(null)
  const [form] = Form.useForm()
  const { currentDeviceId, setCurrentDeviceId } = useDeviceContext()

  const load = () => getDevices().then((r) => setItems(r.data || [])).catch(() => {})
  // 本机卡的数据源 = GET /api/system/info（与 POST /api/system/probe 是同一份采集函数：两次都现场
  // 跑一遍本机版本探测，都不碰远端 probe 缓存，差别只在 HTTP 语义）
  const loadLocal = () => getSystemInfo().then((r) => setLocal(r.data)).catch(() => {})
  // 批次 BD：F5 刷新（scripthub:refresh）与挂载取数同路（重探仍走卡片上的「重新探测」）
  useEffect(() => {
    load()
    loadLocal()
    window.addEventListener('scripthub:refresh', load)
    window.addEventListener('scripthub:refresh', loadLocal)
    return () => {
      window.removeEventListener('scripthub:refresh', load)
      window.removeEventListener('scripthub:refresh', loadLocal)
    }
  }, [])

  const busy = connecting !== null || testing !== null
  const localCur = currentDeviceId === null

  /** 状态点 title：说清「在线」是缓存结论、会过期（这正是「按在线筛选没意义」的误会来源）。
   *  批次 AQ：补上**结论年龄**（后端新加的 probed_age_ms）——「在线」和「结论多旧」是两回事，
   *  后者正是「在线状态过段时间会没掉」那个抱怨的解药。措辞沿用本仓既有口径（「N 分钟前」，
   *  见 DesktopSystemInfo.ago / pages/SystemInfo.tsx）。
   *  ⚠️ 这个数字是**取列表那一刻**的快照（条目只带年龄、不带时间点），不会自己长大 →
   *  所以写「缓存」，不写成「刚刚/现在」。 */
  const ageTip = (d: DeviceItem) => {
    const ms = d.probed_age_ms
    if (ms == null) return ''
    return ms < 60000 ? '，缓存于刚刚' : `，缓存于 ${Math.round(ms / 60000)} 分钟前`
  }
  const dotTitle = (d: DeviceItem) =>
    d.online === true
      ? `设备信息最近一次探测成功（${TTL}内有效）${ageTip(d)}`
      : d.online === false
        ? `设备信息最近一次探测失败${d.last_error ? `：${d.last_error}` : ''}（${TTL}内有效）${ageTip(d)}`
        : `设备信息未探测或结论已过期（探测结果保留 ${TTL}）`

  const doTest = async (d: DeviceItem) => {
    if (busy) return
    setTesting(d.id)
    try {
      const { data } = await testDevice(d.id)
      setResult((p) => ({ ...p, [d.id]: { ok: !!data.ok, kind: 'test', msg: data.ok ? `${data.platform || ''} ${data.os_info || ''}${data.latency_ms ? ` · ${data.latency_ms}ms` : ''}`.trim() : (data.message || '连接失败') } }))
      load()
    } catch {
      setResult((p) => ({ ...p, [d.id]: { ok: false, kind: 'test', msg: '请求失败' } }))
    } finally { setTesting(null) }
  }

  /**
   * 连接 = 先探测，通了才设为当前设备（用户拍板；失败只报错、不切换）。
   * 探测走 probeOnce（GET /{id}/probe）而不是 testDevice：
   *   ① 失败语义 = 「不通」：首探失败（连不上 / 认证失败）→ 后端 502 → 这里 reject，天然就是门禁；
   *      单项运行时探测失败不会把设备判成不通（remote_probe.ok 只由首探决定）。
   *   ② 与切设备后 AM 的自动重探**同一端点、同一 in-flight 去重键**，不会出现两个端点对同一台
   *      设备给出互相矛盾的结论。
   *   ③ testDevice 失败是 200 + ok 字段（要自己解析），且「握手成功但平台探测失败」时它返回
   *      ok=true —— 那是「能连上」，不是「设备可用」，做门禁要多一层判断。
   * 代价：probeOnce 不测握手延时（后端 cache_probe 合并语义保留上次 test 的 latency_ms）→
   *   卡片上的延迟角标仍由「测试连接」刷新。
   */
  const doConnect = async (d: DeviceItem) => {
    if (busy) return
    setConnecting(d.id)
    setResult((p) => { const { [d.id]: _drop, ...rest } = p; return rest })   // 清掉上一轮就地反馈
    try {
      await probeOnce(d.id)
      setCurrentDeviceId(d.id)   // 唯一咽喉：重探 / refreshDevices / devices-changed 都由它内部处理
      // 批次 AO（用户拍板）：连接成功**不留**就地反馈行 —— 「已设为当前设备」是噪音，
      // 按钮自己会变成「已连接」、卡片也会高亮。上面已清掉上一轮反馈，成功路径什么都不用写。
      // 失败仍留一行报错（见 catch）：那时才有用户需要读的信息。
    } catch (e: any) {
      // 失败绝不进入 setCurrentDeviceId —— 当前设备保持原样
      setResult((p) => ({ ...p, [d.id]: { ok: false, kind: 'connect', msg: e?.response?.data?.detail || e?.message || '连接失败' } }))
    } finally {
      setConnecting(null)
      load()   // 探测成功/失败都会写后端 probe 缓存 → 状态点与「平台探测 / 最近失败」跟着更新
    }
  }

  /** 本机「连接」= 切回本机（本机一直在，无需先探测）；reprobe(null) 由 setCurrentDeviceId 内部触发 */
  const connectLocal = () => { if (!busy && !localCur) setCurrentDeviceId(null) }

  const submit = async () => {
    const v: any = await form.validateFields()
    const { credential, ...rest } = v
    // 后端字段：auth_type=password → password；key → private_key（值入 secret store，不回显）
    const payload: Record<string, unknown> = { ...rest }
    if (credential) payload[v.auth_type === 'password' ? 'password' : 'private_key'] = credential
    if (editing) { await updateDevice(editing.id, payload); message.success('已保存') }
    else { await createDevice(payload); message.success('已添加') }
    setOpen(false); setEditing(null); form.resetFields(); load()
  }

  // 页头统计 = 全量（机群概况，与筛选无关）；筛选结果数在筛选行右侧
  const online = items.filter((d) => d.online === true).length
  const probed = items.filter((d) => d.os_info)
  const kw = q.trim().toLowerCase()
  const filtered = items.filter((d) =>
    (!kw || d.name.toLowerCase().includes(kw)) && (!plat || platformOf(d.type) === plat))

  return (
    <>
      <div className="pagehead">
        <b>远程设备</b>
        <span className="cnt" title={`在线 = 最近 ${TTL}内探测成功（缓存结论），不是实时状态`}>
          {items.length} 台 · {online} 在线{probed.length ? ` · 已探测 ${probed.length}` : ''}
        </span>
      </div>
      <div className="filterbar">
        <input className="sel" style={{ width: 200 }} placeholder="搜索设备名…" data-testid="dev-search"
          value={q} onChange={(e) => setQ(e.target.value)} />
        <Sel width={130} testid="dev-platform" value={plat} onChange={setPlat}
          options={[{ value: '', label: '平台：全部' }, { value: 'windows', label: 'Windows' }, { value: 'unix', label: 'Unix / Linux' }]} />
        <span className="mono" style={{ marginLeft: 'auto' }}>{filtered.length} / {items.length} 台</span>
      </div>
      <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
        <div className="cards">
          {/* 批次 AN③：本机卡 —— 不是 items 里的一条记录，单独渲染，**不参与筛选**（搜「windows」不该把
              唯一的「切回本机」入口藏起来）。默认永在第一位：用户切到远程后必须有明确的回头路。
              统计口径不含本机（N 台 = 被管理的远程设备数）。 */}
          <div className={`dcard${localCur ? ' cur' : ''}`} data-testid="dcard-local">
            <div className="dh">
              <span className="dot" style={{ background: 'var(--ok)' }} title="本机恒在线" />
              <b>本机</b>
              <span className="tag">{local ? platformOf(local.platform) : 'unix'}</span>
            </div>
            <div className="row"><span>主机</span><span className="mono">{local?.hostname || '—'}</span></div>
            <div className="row"><span>操作系统</span><span>{local ? `${local.os} ${local.os_version || ''}`.trim() : '—'}</span></div>
            <div className="row"><span>IP 地址</span><span className="mono">{local?.ip || '—'}</span></div>
            <div className="row"><span>运行时</span><span>
              {local ? `${(local.runtimes || []).filter((r) => r.installed).length} / ${(local.runtimes || []).length} 就绪` : '—'}
            </span></div>
            <div className="ops" style={busy ? { pointerEvents: 'none', opacity: 0.6 } : undefined}>
              <span className={`btn${localCur ? ' off' : ''}`} data-testid="connect-local"
                style={{ cursor: localCur ? 'default' : 'pointer', pointerEvents: localCur ? 'none' : undefined }}
                onClick={connectLocal}>{localCur ? '已连接' : '连接'}</span>
            </div>
          </div>
          {filtered.map((d) => {
            const off = d.online === false
            const cur = currentDeviceId === d.id
            const r = result[d.id]
            return (
              <div key={d.id} className={`dcard${cur ? ' cur' : ''}`} data-testid={`dcard-${d.id}`}
                style={off ? { opacity: 0.6 } : undefined}>
                <div className="dh">
                  <span className="dot" title={dotTitle(d)} style={{ background: d.online === true ? 'var(--ok)' : off ? 'var(--err)' : 'var(--muted)' }} />
                  <b>{d.name}</b>
                  <span className="tag">{platformOf(d.type)}</span>
                  {d.latency_ms != null && !off && (
                    <span className="mono" style={{ marginLeft: 'auto', color: 'var(--ok)' }}>{d.latency_ms}ms</span>
                  )}
                </div>
                <div className="row"><span>主机</span><span className="mono">{d.host}:{d.port}</span></div>
                <div className="row"><span>用户 / 认证</span><span>{d.username} · {d.auth_type === 'password' ? '密码' : '密钥'}</span></div>
                {off
                  ? <div className="row"><span>最近失败</span><span className="err">{d.last_error || '连接失败'}</span></div>
                  : <div className="row"><span>平台探测</span><span className="mono">{d.os_info || '未探测'}</span></div>}
                <div className="row"><span>定时任务</span><span>{d.schedule_count || 0} 个{d.delegated_count ? `（${d.delegated_count} 下放）` : ''}</span></div>
                {r && (
                  <div className={`row ${r.ok ? 'ok' : 'err'}`} style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span>{r.kind === 'connect' ? '连接' : '测试连接'}</span><span>{r.msg}</span>
                  </div>
                )}
                <div className="ops" style={busy ? { pointerEvents: 'none', opacity: 0.6 } : undefined}>
                  {/* 连接中：antd Spin（仓库既有加载件，ScriptWorkspace/DesktopShell 都用它）+ 按钮禁用防连点 */}
                  <span className={`btn${cur ? ' off' : ''}`} data-testid={`connect-${d.id}`}
                    style={{
                      cursor: cur ? 'default' : 'pointer', pointerEvents: cur ? 'none' : undefined,
                      display: 'inline-flex', alignItems: 'center', gap: 6,
                    }}
                    onClick={() => doConnect(d)}>
                    {connecting === d.id ? <><Spin size="small" />连接中…</> : cur ? '已连接' : '连接'}
                  </span>
                  <span className="btn" data-testid={`test-${d.id}`} style={{ cursor: 'pointer' }}
                    onClick={() => doTest(d)}>{testing === d.id ? '测试中…' : '测试连接'}</span>
                  <span className="btn" style={{ cursor: 'pointer' }} onClick={() => { setEditing(d); form.setFieldsValue(d); setOpen(true) }}>编辑</span>
                  <span className="btn danger" style={{ cursor: 'pointer' }}
                    onClick={() => Modal.confirm({ title: `删除设备 ${d.name}？`, onOk: async () => { await deleteDevice(d.id); load() } })}>删除</span>
                </div>
              </div>
            )
          })}
          {/* v2.8（用户）：新增设备入口收进这张「+」卡片（页头按钮已移除） */}
          <div className="dcard" role="button" tabIndex={0} title="新增远程设备"
            onClick={() => { setEditing(null); form.resetFields(); setOpen(true) }}
            style={{ borderStyle: 'dashed', display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 150, color: 'var(--muted)', fontSize: 13, flexDirection: 'column', gap: 6, textAlign: 'center', cursor: 'pointer' }}>
            <span style={{ fontSize: 20 }}>+</span>
            <span>添加远程设备后<br />即可远程执行脚本</span>
          </div>
        </div>
      </div>

      <Modal title={editing ? '编辑设备' : '添加设备'} open={open} onCancel={() => setOpen(false)} onOk={submit} width={480} style={{ top: 60 }}>
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="type" label="类型" initialValue="linux">
            <Select options={[{ value: 'linux', label: 'unix' }, { value: 'mac', label: 'mac' }, { value: 'windows', label: 'windows' }]} />
          </Form.Item>
          <Form.Item name="host" label="主机" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="port" label="端口" initialValue={22}><Input /></Form.Item>
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="auth_type" label="认证方式" initialValue="password">
            <Select options={[{ value: 'key', label: '密钥' }, { value: 'password', label: '密码' }]} />
          </Form.Item>
          <Form.Item name="credential" label="凭据（密码，或私钥内容）" extra="值存入 secret store，不会回显">
            <Input.Password />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
