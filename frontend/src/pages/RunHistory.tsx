import { useState, useEffect } from 'react'
import { Card, Table, Tag, Button, Space, message, Tooltip, Modal } from 'antd'
import { ReloadOutlined, DownloadOutlined, EyeOutlined } from '@ant-design/icons'
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

  const showOutput = (output: string) => {
    setSelectedOutput(output || '无输出')
    setOutputModalVisible(true)
  }

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
      render: (command: string) => (
        <Tooltip title={command}>
          <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{command}</span>
        </Tooltip>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => (
        <Tag color={STATUS_COLORS[status]}>{STATUS_LABELS[status] || status}</Tag>
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
    {
      title: '操作',
      key: 'actions',
      width: 150,
      render: (_: unknown, record: RunHistoryItem) => (
        <Space>
          <Tooltip title="查看输出">
            <Button
              size="small"
              icon={<EyeOutlined />}
              onClick={() => showOutput(record.output)}
            >
              查看
            </Button>
          </Tooltip>
          {record.output_file && (
            <Tooltip title="下载完整日志">
              <Button
                size="small"
                icon={<DownloadOutlined />}
                href={`/api/run/${record.id}/download`}
                target="_blank"
              >
                日志
              </Button>
            </Tooltip>
          )}
        </Space>
      ),
    },
  ]

  return (
    <>
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
        title="脚本输出"
        open={outputModalVisible}
        onCancel={() => setOutputModalVisible(false)}
        footer={null}
        width={800}
        styles={{ body: { maxHeight: '60vh', overflow: 'auto' } }}
      >
        <pre style={{ 
          background: '#1e1e1e', 
          color: '#d4d4d4', 
          padding: 16, 
          borderRadius: 6,
          fontFamily: 'Consolas, "Courier New", monospace',
          fontSize: 13,
          lineHeight: 1.5,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-all'
        }}>
          {selectedOutput}
        </pre>
      </Modal>
    </>
  )
}
