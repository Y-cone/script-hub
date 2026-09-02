import { useEffect, useState } from 'react'
import { Card, Descriptions, Table, Tag, Alert } from 'antd'
import { getSystemInfo } from '../services/api'
import type { SystemInfo } from '../services/api'

export default function SystemInfo() {
  const [info, setInfo] = useState<SystemInfo | null>(null)

  useEffect(() => {
    getSystemInfo().then((res) => setInfo(res.data)).catch(() => {})
  }, [])

  if (!info) return <Card loading />

  const runtimeColumns = [
    { title: '运行时', dataIndex: 'name', key: 'name', width: 120 },
    {
      title: '状态',
      dataIndex: 'installed',
      key: 'installed',
      width: 90,
      render: (v: boolean) => (v ? <Tag color="success">已安装</Tag> : <Tag color="default">未安装</Tag>),
    },
    { title: '版本', dataIndex: 'version', key: 'version', render: (v: string | null) => v ?? '-' },
  ]

  return (
    <div style={{ flex: 1, overflow: 'auto' }}>
      <Card title="设备信息" size="small" style={{ marginBottom: 16 }}>
        <Descriptions column={2} size="small" bordered>
          <Descriptions.Item label="主机名">{info.hostname}</Descriptions.Item>
          <Descriptions.Item label="操作系统">{info.os} (release {info.os_version})</Descriptions.Item>
          <Descriptions.Item label="架构">{info.arch}</Descriptions.Item>
          <Descriptions.Item label="平台类型">
            <Tag color={info.platform === 'windows' ? 'blue' : 'green'}>{info.platform}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="本机 IP">{info.ip || '-'}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="运行时环境" size="small">
        {info.platform === 'unix' && (
          <Alert
            type="info"
            showIcon
            message="当前平台为 Unix，.bat / .ps1 脚本在此平台无法直接执行"
            style={{ marginBottom: 16 }}
          />
        )}
        <Table
          dataSource={info.runtimes}
          columns={runtimeColumns}
          rowKey="name"
          size="small"
          pagination={false}
        />
      </Card>
    </div>
  )
}