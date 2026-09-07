import { useEffect, useState } from 'react'
import { Card, Descriptions, Table, Tag, Alert } from 'antd'
import { getSystemInfo, probeDevice } from '../services/api'
import { useDeviceContext } from '../stores/deviceContext'

interface DisplayInfo {
  title: string
  hostname: string
  os: string
  os_version?: string
  arch?: string
  platform: string
  ip?: string
  type?: string
  runtimes: { name: string; installed: boolean; version: string | null }[]
}

export default function SystemInfo() {
  const { currentDeviceId } = useDeviceContext()
  const [info, setInfo] = useState<DisplayInfo | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setInfo(null)
    setError('')
    setLoading(true)
    const id = currentDeviceId
    const timer = setTimeout(() => {
      // 探测超过 15s 视为失败（慢/不可达设备），避免永久加载
      setLoading(false)
      setError(`无法获取 ${id ? '远程设备' : '本机'} 信息（超时）`)
      setInfo(null)
    }, 15000)
    if (id) {
      probeDevice(id)
        .then((res) => {
          clearTimeout(timer)
          const d = res.data
          setLoading(false)
          setInfo({
            title: `远程设备：${d.name} (${d.host})`,
            hostname: d.name,
            os: d.os || '未知',
            platform: d.platform,
            type: d.type,
            runtimes: d.runtimes || [],
          })
        })
        .catch((e: any) => {
          clearTimeout(timer)
          setLoading(false)
          setError(`无法连接设备：${e?.response?.data?.detail || e?.message || '连接失败'}`)
          setInfo(null)
        })
    } else {
      getSystemInfo()
        .then((res) => {
          clearTimeout(timer)
          const d = res.data
          setLoading(false)
          setInfo({
            title: '本机信息',
            hostname: d.hostname,
            os: d.os,
            os_version: d.os_version,
            arch: d.arch,
            platform: d.platform,
            ip: d.ip,
            runtimes: d.runtimes || [],
          })
        })
        .catch(() => {
          clearTimeout(timer)
          setLoading(false)
          setError('获取本机信息失败')
          setInfo(null)
        })
    }
    return () => clearTimeout(timer)
  }, [currentDeviceId])

  if (loading) return <Card loading />
  if (error) return <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />
  if (!info) return null

  const runtimeColumns = [
    { title: '运行时', dataIndex: 'name', key: 'name', width: 120 },
    {
      title: '状态', dataIndex: 'installed', key: 'installed', width: 90,
      render: (v: boolean) => (v ? <Tag color="success">已安装</Tag> : <Tag color="default">未安装</Tag>),
    },
    { title: '版本', dataIndex: 'version', key: 'version', render: (v: string | null) => v ?? '-' },
  ]

  return (
    <div style={{ flex: 1, overflow: 'auto' }}>
      <Card title={info.title} size="small" style={{ marginBottom: 16 }}>
        <Descriptions column={2} size="small" bordered>
          <Descriptions.Item label="主机名">{info.hostname}</Descriptions.Item>
          <Descriptions.Item label="操作系统">
            {info.os}{info.os_version ? ` (release ${info.os_version})` : ''}
          </Descriptions.Item>
          {info.arch && <Descriptions.Item label="架构">{info.arch}</Descriptions.Item>}
          {info.type && <Descriptions.Item label="设备类型"><Tag color="green">{info.type}</Tag></Descriptions.Item>}
          <Descriptions.Item label="平台类型">
            <Tag color={info.platform === 'windows' ? 'blue' : 'green'}>{info.platform || '-'}</Tag>
          </Descriptions.Item>
          {info.ip && <Descriptions.Item label="本机 IP">{info.ip}</Descriptions.Item>}
        </Descriptions>
      </Card>

      <Card title="运行时环境" size="small">
        {info.platform === 'unix' && (
          <Alert
            type="info" showIcon
            message="当前平台为 Unix，.bat / .ps1 脚本在此平台无法直接执行"
            style={{ marginBottom: 16 }}
          />
        )}
        <Table dataSource={info.runtimes} columns={runtimeColumns} rowKey="name" size="small" pagination={false} />
      </Card>
    </div>
  )
}