import { useEffect, useState, useCallback } from 'react'
import { Table, Button, Switch, Space, Modal, Form, Input, Select, InputNumber, Popconfirm, message, Radio, Tag } from 'antd'
import { PlusOutlined, CaretRightOutlined, DeleteOutlined, EditOutlined, HistoryOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { getSchedules, createSchedule, updateSchedule, deleteSchedule, runScheduleNow, getScripts } from '../services/api'
import type { ScheduleItem, ScriptItem } from '../services/api'

export default function Schedules() {
  const [items, setItems] = useState<ScheduleItem[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<ScheduleItem | null>(null)
  const [scripts, setScripts] = useState<ScriptItem[]>([])
  const [form] = Form.useForm()
  const navigate = useNavigate()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const { data } = await getSchedules()
      setItems(data.items)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    getScripts({ page_size: 100 }).then((res) => setScripts(res.data.items)).catch(() => {})
  }, [])

  const handleToggle = async (s: ScheduleItem, enabled: boolean) => {
    try {
      await updateSchedule(s.id, { enabled })
      message.success(enabled ? '已启用' : '已暂停')
      load()
    } catch {
      message.error('操作失败')
    }
  }

  const handleRun = async (s: ScheduleItem) => {
    try {
      await runScheduleNow(s.id)
      message.success('已触发执行')
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '触发失败')
    }
  }

  const handleDelete = async (s: ScheduleItem) => {
    try {
      await deleteSchedule(s.id)
      message.success('已删除')
      load()
    } catch {
      message.error('删除失败')
    }
  }

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ type: 'interval', interval_seconds: 3600 })
    setModalOpen(true)
  }

  const openEdit = (s: ScheduleItem) => {
    setEditing(s)
    form.resetFields()
    form.setFieldsValue({
      name: s.name,
      script_id: s.script_id,
      type: s.cron_expr ? 'cron' : 'interval',
      cron_expr: s.cron_expr,
      interval_seconds: s.interval_seconds,
      timeout: s.timeout,
    })
    setModalOpen(true)
  }

  const scriptOptions = scripts.map((s) => ({ label: s.name, value: s.id }))

  const handleSubmit = async () => {
    try {
      const v = await form.validateFields()
      const data: any = { name: v.name, script_id: v.script_id, timeout: v.timeout || 0 }
      if (v.type === 'cron') {
        data.cron_expr = v.cron_expr
        data.interval_seconds = 0
      } else {
        data.interval_seconds = v.interval_seconds
        data.cron_expr = ''
      }
      if (editing) {
        await updateSchedule(editing.id, data)
        message.success('保存成功')
      } else {
        await createSchedule(data)
        message.success('创建成功')
      }
      setModalOpen(false)
      load()
    } catch (e: any) {
      if (e?.response?.data?.detail) message.error(e.response.data.detail)
    }
  }

  const columns: any[] = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 50 },
    { title: '名称', dataIndex: 'name', key: 'name' },
    {
      title: '脚本',
      dataIndex: 'script_name',
      key: 'script_name',
      width: 180,
      render: (v: string | null, r: ScheduleItem) => v || `#${r.script_id}（已删除）`,
    },
    {
      title: '目录',
      dataIndex: 'script_path',
      key: 'script_path',
      render: (v: string | null) => (v ? <Tag>{v}</Tag> : '-'),
    },
    {
      title: '触发方式',
      key: 'trigger',
      width: 130,
      render: (_: unknown, r: ScheduleItem) => r.cron_expr
        ? <Tag color="blue">{r.cron_expr}</Tag>
        : <Tag color="green">每 {r.interval_seconds}s</Tag>,
    },
    {
      title: '启用',
      key: 'enabled',
      width: 60,
      render: (_: unknown, r: ScheduleItem) => (
        <Switch size="small" checked={r.enabled} onChange={(v) => handleToggle(r, v)} />
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 230,
      render: (_: unknown, r: ScheduleItem) => (
        <Space size="small">
          <Button size="small" icon={<CaretRightOutlined />} onClick={() => handleRun(r)}>执行</Button>
          <Button size="small" icon={<HistoryOutlined />} onClick={() => navigate(`/history?schedule_id=${r.id}`)}>历史</Button>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)} />
          <Popconfirm title="删除此调度？" onConfirm={() => handleDelete(r)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'flex-end' }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新建调度
        </Button>
      </div>

      <Table
        dataSource={items}
        columns={columns}
        rowKey="id"
        loading={loading}
        size="small"
        pagination={false}
        locale={{ emptyText: '暂无调度任务' }}
      />

      <Modal
        title={editing ? '编辑调度' : '新建调度'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={handleSubmit}
        width={520}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input placeholder="如：每日定时备份" />
          </Form.Item>
          <Form.Item name="script_id" label="脚本" rules={[{ required: true }]}>
            <Select options={scriptOptions} placeholder="选择脚本" showSearch optionFilterProp="label" />
          </Form.Item>
          <Form.Item name="type" label="触发类型">
            <Radio.Group>
              <Radio value="interval">间隔</Radio>
              <Radio value="cron">Cron</Radio>
            </Radio.Group>
          </Form.Item>
          <Form.Item noStyle shouldUpdate>
            {({ getFieldValue }) => (
              getFieldValue('type') === 'cron' ? (
                <Form.Item name="cron_expr" label="Cron 表达式" rules={[{ required: true }]}>
                  <Input placeholder="如：0 9 * * *（每天9点）" />
                </Form.Item>
              ) : (
                <Form.Item name="interval_seconds" label="间隔（秒）" rules={[{ required: true }]}>
                  <InputNumber min={1} style={{ width: '100%' }} />
                </Form.Item>
              )
            )}
          </Form.Item>
          <Form.Item name="timeout" label="超时（秒，0=不限）">
            <InputNumber min={0} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}