import { useState, useEffect } from 'react'
import { Card, Table, Tag, Button, Space, message, Tooltip, Modal, Statistic, Row, Col } from 'antd'
import { ReloadOutlined, DownloadOutlined, EyeOutlined, CheckCircleOutlined, CloseCircleOutlined, ClockCircleOutlined } from '@ant-design/icons'
import { getRunHistory } from '../services/api'
import type { RunHistoryItem } from '../services/api'

const STATUS_COLORS: Record<string, string> = {
  running: 'processing',
  success: 'success',
  failed: 'error',
  killed: 'warning',
  timeout: 'error',
}

const STATUS_LABELS: Record<string, string> = {
  running: '运行中',
  success: '成功',
  failed: '失败',
  killed: '已终止',
  timeout: '超时',
}

export default function RunHistory() {
  const [data, setData] = useState<RunHistoryItem[]>([])
  const [loading, setLoading] = useState(false)
  const [pagination, setPagination] = useState({
    current: 1,
    pageSize: 20,
    total: 0,
  })
  const [outputModalVisible, setOutputModalVisible] = useState(false)
  const [selectedOutput, setSelectedOutput] = useState('')
  const [selectedCommand, setSelectedCommand] = useState('')

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

  const showOutput = (record: RunHistoryItem) => {
    setSelectedOutput(record.output || '无输出')
    setSelectedCommand(record.command || '')
    setOutputModalVisible(true)
  }

  const columns = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 70,
    },
    {
      title: '脚本ID',
      dataIndex: 'script_id',
      key: 'script_id',
      width: 80,
    },
    {
      title: '执行命令',
      dataIndex: 'command',
      key: 'command',
      ellipsis: true,
      render: (command: string) => (
        <Tooltip title={command}>
          <code style={{ 
            background: '#f5f5f5', 
            padding: '2px 6px', 
            borderRadius: 4,
            fontSize: 12,
          }}>
            {command}
          </code>
        </Tooltip>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (status: string) => (
        <Tag color={STATUS_COLORS[status]}>{STATUS_LABELS[status] || status}</Tag>
      ),
    },
    {
      title: '退出码',
      dataIndex: 'exit_code',
      key: 'exit_code',
      width: 80,
      render: (code: number | null) => {
        if (code === null || code === undefined) return '-'
        return code === 0 
          ? <span style={{ color: '#52c41a' }}>{code}</span>
          : <span style={{ color: '#ff4d4f' }}>{code}</span>
      },
    },
    {
      title: '耗时',
      dataIndex: 'duration',
      key: 'duration',
      width: 90,
      render: (duration: number | null) => {
        if (!duration) return '-'
        return duration < 1 
          ? `${(duration * 1000).toFixed(0)}ms`
          : `${duration.toFixed(2)}s`
      },
    },
    {
      title: '开始时间',
      dataIndex: 'started_at',
      key: 'started_at',
      width: 170,
      render: (time: string) => new Date(time).toLocaleString(),
    },
    {
      title: '操作',
      key: 'actions',
      width: 140,
      render: (_: unknown, record: RunHistoryItem) => (
        <Space size="small">
          <Button
            size="small"
            icon={<EyeOutlined />}
            onClick={() => showOutput(record)}
          >
            输出
          </Button>
          {record.output_file && (
            <Button
              size="small"
              icon={<DownloadOutlined />}
              href={`/api/run/${record.id}/download`}
              target="_blank"
            >
              日志
            </Button>
          )}
        </Space>
      ),
    },
  ]

  // 统计
  const stats = data.reduce((acc, item) => {
    acc[item.status] = (acc[item.status] || 0) + 1
    return acc
  }, {} as Record<string, number>)

  return (
    <>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="总运行次数"
              value={pagination.total}
              prefix={<ClockCircleOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="成功"
              value={stats.success || 0}
              valueStyle={{ color: '#3f8600' }}
              prefix={<CheckCircleOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="失败"
              value={stats.failed || 0}
              valueStyle={{ color: '#cf1322' }}
              prefix={<CloseCircleOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="已终止"
              value={stats.killed || 0}
              valueStyle={{ color: '#d48806' }}
              prefix={<CloseCircleOutlined />}
            />
          </Card>
        </Col>
      </Row>

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
            showSizeChanger: true,
            showTotal: (total) => `共 ${total} 条记录`,
            onChange: (page, pageSize) => fetchData(page, pageSize),
          }}
        />
      </Card>

      <Modal
        title={
          <Space>
            <span>脚本输出</span>
            {selectedCommand && (
              <code style={{ fontSize: 12, color: '#666' }}>{selectedCommand}</code>
            )}
          </Space>
        }
        open={outputModalVisible}
        onCancel={() => setOutputModalVisible(false)}
        footer={null}
        width={900}
        styles={{ body: { maxHeight: '65vh', overflow: 'auto' } }}
      >
        <pre style={{ 
          background: '#1e1e1e', 
          color: '#d4d4d4', 
          padding: 16, 
          borderRadius: 6,
          fontFamily: 'Consolas, "Courier New", monospace',
          fontSize: 13,
          lineHeight: 1.6,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-all',
          margin: 0,
        }}>
          {selectedOutput}
        </pre>
      </Modal>
    </>
  )
}
