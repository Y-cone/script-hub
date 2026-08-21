import { useState, useEffect } from 'react'
import { Card, Table, Tag, Button, Space, message } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { getRunHistory } from '../services/api'
import type { RunHistoryItem } from '../services/api'

const STATUS_COLORS: Record<string, string> = {
  running: 'processing',
  success: 'success',
  failed: 'error',
  killed: 'warning',
  timeout: 'error',
}

export default function RunHistory() {
  const [data, setData] = useState<RunHistoryItem[]>([])
  const [loading, setLoading] = useState(false)
  const [pagination, setPagination] = useState({
    current: 1,
    pageSize: 20,
    total: 0,
  })

  const fetchData = async (page = 1, pageSize = 20) => {
    setLoading(true)
    try {
      const result = await getRunHistory({ page, page_size: pageSize })
      setData(result.data.items)
      setPagination({
        current: page,
        pageSize,
        total: result.data.total,
      })
    } catch (error) {
      message.error('获取运行历史失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
  }, [])

  const columns = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 80,
    },
    {
      title: '脚本ID',
      dataIndex: 'script_id',
      key: 'script_id',
      width: 100,
    },
    {
      title: '命令',
      dataIndex: 'command',
      key: 'command',
      ellipsis: true,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => (
        <Tag color={STATUS_COLORS[status]}>{status}</Tag>
      ),
    },
    {
      title: '退出码',
      dataIndex: 'exit_code',
      key: 'exit_code',
      width: 100,
      render: (code: number | null) => code ?? '-',
    },
    {
      title: '耗时(秒)',
      dataIndex: 'duration',
      key: 'duration',
      width: 100,
      render: (duration: number | null) => duration?.toFixed(2) ?? '-',
    },
    {
      title: '开始时间',
      dataIndex: 'started_at',
      key: 'started_at',
      width: 180,
      render: (time: string) => new Date(time).toLocaleString(),
    },
  ]

  return (
    <Card
      title="运行历史"
      extra={
        <Button
          icon={<ReloadOutlined />}
          onClick={() => fetchData(pagination.current, pagination.pageSize)}
        >
          刷新
        </Button>
      }
    >
      <Table
        dataSource={data}
        columns={columns}
        rowKey="id"
        loading={loading}
        pagination={{
          ...pagination,
          onChange: (page, pageSize) => fetchData(page, pageSize),
        }}
      />
    </Card>
  )
}