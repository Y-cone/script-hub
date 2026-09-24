/**
 * V5-G 桌面形态 · §5 本机信息（1:1 复刻 mockup §5）
 * 页头（主机名 · 探测于 X 前 + ↻ 重新探测）+ sysgrid 双卡（系统概览 kv / 运行时环境就绪点）。
 *
 * G1-A：跟随设备上下文——切设备立即重探（本机 probeSystem / 远程 probeDevice），
 * 失败/超时在页内可见（深色错误卡，非 antd Alert）。
 *
 * 批次 AM：重探入口提升为全局（stores/deviceContext.reprobe）后，本页改成调 `probeOnce`——
 * 与全局那份**共用同一个在飞行中的请求**（切设备时不会探两遍），本页仍拿得到载荷渲染双卡。
 *
 * 批次 AO（用户报的 ①）：「共用 in-flight」只免掉同一刻的重复请求，**进页面仍会真发一次探测**
 * （而切设备时已经探过一次了）→ 改成**缓存优先**：
 *   · 远程设备：后端 probe 缓存新鲜（GET /api/devices 的 `os_info`/`runtimes`/`platform` 就是
 *     `fresh_entry` 那份结论，TTL 600s）→ 直接拿它渲染，**一个探测请求都不发**；没有/过期才探。
 *   · 本机：**没有** probe 缓存（本机采集是纯本地调用，不进缓存）→ 保持每次进页面取一次，
 *     它既不连网也不碰 SSH。
 *   · 右上角「↻ 重新探测」= 用户明确要求刷新 → **绕过缓存**，必真探。
 *
 * 批次 AQ：页头「探测于 X 前」的来源（此前这条注释承诺的「照旧保留」不成立 —— 远端两个分支
 * 都拿不到 probed_at，页头对远程设备一个年龄都不显示）：
 *   · 本机：服务端 GET/POST /api/system/* 直接回 `probed_at`（墙钟）。
 *   · 远程：缓存分支用后端新加的 `probed_age_ms`（**年龄**，不是时间点 —— 缓存 ts 是
 *     `time.monotonic()`）换算成「现在 - 年龄」的 ISO；force 分支是刚探完，用本地时刻。
 *     两者都喂给同一个 `ago()`，所以数字会随时间自己长大（不是取数那一刻冻住的）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { probeOnce } from '../../services/api'
import type { RuntimeItem } from '../../services/api'
import { useDeviceContext } from '../../stores/deviceContext'

/** 探测上限：慢/不可达设备不无限转圈（沿用 LegacySystemInfo 的 15s） */
const PROBE_TIMEOUT = 15000

/** 双卡统一展示模型：本机（probeSystem）与远程（probeDevice）字段并集 */
interface Display {
  remote: boolean
  hostname: string
  os: string
  os_version?: string
  arch?: string
  platform?: string
  ip?: string
  type?: string
  data_dir?: string
  scripts_root?: string
  probed_at?: string
  runtimes: RuntimeItem[]
}

function ago(iso?: string) {
  if (!iso) return ''
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return `${Math.floor(s)} 秒前`
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`
  return `${Math.floor(s / 3600)} 小时前`
}

/** 结论**年龄**（毫秒）→ ISO 时刻（批次 AQ）。后端只给年龄不给时间点（缓存 ts 是 monotonic，
 *  见 DeviceOut 注释），所以在这里换算：`ago()` 是渲染时现算的，数字会随时间自己长大 ——
 *  页头读到的是「这份缓存结论现在有多旧」，不是取数那一刻冻住的快照。 */
function ageToIso(ms?: number | null) {
  return ms == null ? undefined : new Date(Date.now() - ms).toISOString()
}

export default function DesktopSystemInfo() {
  const { currentDeviceId, refreshDevices } = useDeviceContext()
  const [info, setInfo] = useState<Display | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const seq = useRef(0)          // 代次：丢弃上一个设备的迟到响应 / 已作废的超时回调
  const timerRef = useRef(0)

  /** 取一次数据并渲染。`force=true`（「↻ 重新探测」）绕过缓存；false = 缓存优先。 */
  const load = useCallback(async (id: number | null, force: boolean) => {
    const my = ++seq.current
    const live = () => my === seq.current
    setInfo(null)                      // 先清旧数据 + 进 loading，避免短暂显示上一台设备
    setError('')
    setLoading(true)
    clearTimeout(timerRef.current)

    // ① 缓存优先（远程）：后端 probe 缓存新鲜 → 用缓存渲染，不发探测请求
    if (id !== null && !force) {
      let d = useDeviceContext.getState().devices.find((x) => x.id === id)
      if (!d) {                        // 名单还没到（冷启动抢首屏）→ 补一次本地名单请求（不碰 SSH）
        await refreshDevices()
        if (!live()) return
        d = useDeviceContext.getState().devices.find((x) => x.id === id)
      }
      if (d && d.os_info) {            // os_info 有值 ⇔ fresh_entry 命中（未过 TTL 600s）
        setInfo({
          remote: true,
          hostname: d.name,
          os: d.os_info,
          platform: d.platform || undefined,
          type: d.type,
          ip: d.host,
          runtimes: d.runtimes || [],
          probed_at: ageToIso(d.probed_age_ms),   // 批次 AQ：页头可见「这份结论有多旧」
        })
        setLoading(false)
        return
      }
      // 无新鲜结论（从未探测 / 已过期 / 上次探测失败）→ 落到下面真探一次：
      // 页内错误卡要拿到可以直接读的原因，且这条路径是用户主动进页面看设备状态。
    }

    // 设备名尽量取最新的（refreshDevices 是异步的，闭包里的映射可能过期）
    const target = () => (id ? useDeviceContext.getState().deviceNames[id] || `设备 #${id}` : '本机')
    timerRef.current = window.setTimeout(() => {
      if (!live()) return
      setLoading(false)
      setError(id ? `探测「${target()}」超时（15 秒无响应）` : `获取本机信息超时（15 秒无响应）`)
    }, PROBE_TIMEOUT)

    const req: Promise<Display> = id !== null
      ? probeOnce(id).then(({ data: d }) => ({
          remote: true,
          hostname: d.name,
          os: d.os || '未知',
          platform: d.platform,
          type: d.type,
          ip: d.host,
          runtimes: d.runtimes || [],
          probed_at: new Date().toISOString(),   // 批次 AQ：这条路径 = 刚探完（年龄≈0）
        }))
      : probeOnce(null).then(({ data: d }) => ({
          remote: false,
          hostname: d.hostname,
          os: d.os,
          os_version: d.os_version,
          arch: d.arch,
          platform: d.platform,
          ip: d.ip,
          runtimes: d.runtimes || [],
          data_dir: d.data_dir,
          scripts_root: d.scripts_root,
          probed_at: d.probed_at,
        }))

    try {
      const d = await req
      if (!live()) return
      clearTimeout(timerRef.current)
      setInfo(d)
      setLoading(false)
    } catch (e: any) {
      if (!live()) return
      clearTimeout(timerRef.current)
      setLoading(false)
      const why = e?.response?.data?.detail || e?.message || '连接失败'
      setError(id ? `无法连接「${target()}」：${why}` : `获取本机信息失败：${why}`)
    }
  }, [refreshDevices])

  // 进页面 / 切设备 → 缓存优先（force=false）
  useEffect(() => {
    void load(currentDeviceId, false)
    // 批次 BD：F5 刷新（scripthub:refresh）同路（force=false 与进页一致，取缓存优先的结论；
    // 绕缓存重探仍是「↻ 重新探测」那条路）
    const onRefresh = () => { void load(currentDeviceId, false) }
    window.addEventListener('scripthub:refresh', onRefresh)
    return () => window.removeEventListener('scripthub:refresh', onRefresh)
  }, [currentDeviceId, load])
  // 卸载清掉待触发的超时（避免已换成别的设备后弹出旧错误）
  useEffect(() => () => { clearTimeout(timerRef.current) }, [])

  /** 「↻ 重新探测」：用户明确要求刷新 → 不走缓存 */
  const reprobeNow = () => { void load(currentDeviceId, true) }

  const probeFail = (rt: RuntimeItem) => !rt.installed

  // 远程探测端点不返回内核/架构/数据目录/脚本根目录 → 只渲染拿得到的行
  const rows: [string, string][] = info
    ? info.remote
      ? [
          ['主机名', info.hostname],
          ['操作系统', info.os],
          ['平台', info.platform || '—'],
          ...(info.ip ? ([['IP 地址', info.ip]] as [string, string][]) : []),
          ['设备类型', info.type || '—'],
        ]
      : [
          ['主机名', info.hostname],
          ['操作系统', info.os],
          ['内核', info.os_version || '—'],
          ['架构', info.arch || '—'],
          ['IP 地址', info.ip || '—'],
          ['数据目录', info.data_dir || '—'],
          ['脚本根目录', info.scripts_root || '—'],
        ]
    : []

  return (
    <>
      <div className="pagehead">
        <b>本机信息</b>
        <span className="cnt">
          {info?.hostname || '—'}
          {info?.remote ? ` · 远程设备${info.type ? `（${info.type}）` : ''}` : ''}
          {info?.probed_at ? ` · 探测于 ${ago(info.probed_at)}` : ''}
        </span>
        <span style={{ marginLeft: 'auto' }}>
          <span
            className="btn"
            data-testid="reprobe"
            style={{ cursor: loading ? 'default' : 'pointer', opacity: loading ? 0.6 : 1, pointerEvents: loading ? 'none' : undefined }}
            onClick={reprobeNow}
          >
            {loading ? '探测中…' : '↻ 重新探测'}
          </span>
        </span>
      </div>
      <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
        {error ? (
          // 失败可见：深色错误卡（原型 §5 皮肤），带设备名 + 原因 + 重试指引
          <div className="scard" data-testid="probe-error" style={{ margin: 14, borderColor: '#5a3a3a' }}>
            <h5 className="err">探测失败</h5>
            <div className="err" style={{ fontSize: 13.5, lineHeight: '20px' }}>{error}</div>
            <div style={{ marginTop: 8, fontSize: 12.5, color: 'var(--muted)' }}>
              可点右上角「↻ 重新探测」重试；远程设备探测走 SSH，请确认目标在线且 SSH 地址/端口/凭据正确。
            </div>
          </div>
        ) : (
          <div className="sysgrid">
            <div className="scard">
              <h5>系统概览</h5>
              {rows.map(([k, v]) => (
                <div className="kv" key={k}>
                  <span>{k}</span>
                  <span style={{ fontSize: k === '数据目录' || k === '脚本根目录' ? 12 : undefined }}>{v}</span>
                </div>
              ))}
              {rows.length === 0 && <span className="mono">{loading ? '探测中…' : '（无探测结果）'}</span>}
            </div>
            <div className="scard">
              <h5>运行时环境（脚本依赖检测）</h5>
              {(info?.runtimes || []).map((rt) => {
                const bad = probeFail(rt)
                return (
                  <div className="rt" key={rt.name}>
                    <span>
                      <span className="dot" style={{ background: bad ? 'var(--err)' : 'var(--ok)', marginRight: 8 }} />
                      {rt.name}
                    </span>
                    <span className={`v${bad ? ' err' : ''}`}>{rt.version || '未安装'}</span>
                  </div>
                )
              })}
              {(info?.runtimes || []).length === 0 && (
                <span className="mono">{loading ? '探测中…' : '（无探测结果）'}</span>
              )}
              <div style={{ marginTop: 10, padding: '8px 10px', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 4, fontSize: 12.5, color: 'var(--muted)' }}>
                状态点 = 脚本环境检测的判定依据；红色项执行依赖它的脚本时会被拦截确认。
              </div>
            </div>
          </div>
        )}
      </div>
    </>
  )
}
