import { useEffect, useState, useCallback } from 'react'
import { Table, Button, Modal, Form, Input, InputNumber, Select, Space, Popconfirm, message, Tag } from 'antd'
import { PlusOutlined, DeleteOutlined, EditOutlined, ReloadOutlined } from '@ant-design/icons'
import { getDevices, createDevice, updateDevice, deleteDevice, testDevice } from '../services/api'
import type { DeviceItem } from '../services/api'

export default function Devices() {
  const [items, setItems] = useState<DeviceItem[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<DeviceItem | null>(null)
  const [testing, setTesting] = useState<number | null>(null)
  const [form] = Form.useForm()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const { data } = await getDevices()
      setItems(data)
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => { load() }, [load])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ type: 'linux', port: 22, auth_type: 'password' })
    setModalOpen(true)
  }
  const openEdit = (d: DeviceItem) => {
    setEditing(d)
    form.resetFields()
    form.setFieldsValue({ name: d.name, type: d.type, host: d.host, port: d.port, auth_type: d.auth_type, username: d.username })
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    try {
      const v = await form.validateFields()
      if (editing) {
        await updateDevice(editing.id, v)
        message.success('已保存')
      } else {
        await createDevice(v)
        message.success('已添加设备')
      }
      setModalOpen(false)
      load()
      // 通知顶部设备上下文下拉刷新
      window.dispatchEvent(new Event('devices-changed'))
    } catch (e: any) {
      if (e?.response?.data?.detail) message.error(e.response.data.detail)
    }
  }

  const handleDelete = async (d: DeviceItem) => {
    await deleteDevice(d.id)
    message.success('已删除')
    load()
    window.dispatchEvent(new Event('devices-changed'))
  }

  const handleTest = async (d: DeviceItem) => {
    setTesting(d.id)
    try {
      const { data } = await testDevice(d.id)
      if (data.ok) {
        message.success(`连接成功 ${data.platform || ''} ${data.os_info || ''}`)
      } else {
        message.error(data.message || '连接失败')
      }
    } finally {
      setTesting(null)
    }
  }

  const columns: any[] = [
    { title: 'ID', dataIndex: 'id', width: 50 },
    { title: '名称', dataIndex: 'name' },
    {
      title: '类型', dataIndex: 'type', width: 90,
      render: (v: string) => (
        v === 'linux' ? <Tag color="green">Linux</Tag>
        : v === 'mac' ? <Tag color="blue">macOS</Tag>
        : <Tag color="purple">Windows</Tag>
      ),
    },
    { title: '主机', dataIndex: 'host' },
    { title: '端口', dataIndex: 'port', width: 70 },
    { title: '用户', dataIndex: 'username', width: 90 },
    {
      title: '认证', dataIndex: 'auth_type', width: 80,
      render: (v: string) => (v === 'key' ? '密钥' : '密码'),
    },
    {
      title: '操作', key: 'actions', width: 220,
      render: (_: unknown, r: DeviceItem) => (
        <Space size="small">
          <Button size="small" icon={<ReloadOutlined spin={testing === r.id} />} onClick={() => handleTest(r)}>测试</Button>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)} />
          <Popconfirm title="删除该设备？" onConfirm={() => handleDelete(r)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'flex-end' }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>添加设备</Button>
      </div>
      <Table dataSource={items} columns={columns} rowKey="id" loading={loading} size="small" pagination={false}
        locale={{ emptyText: '暂无远程设备' }} />

      <Modal title={editing ? '编辑设备' : '添加设备'} open={modalOpen} onCancel={() => setModalOpen(false)} onOk={handleSubmit} width={480}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input placeholder="如：生产服务器1" /></Form.Item>
          <Form.Item name="host" label="主机/IP" rules={[{ required: true }]}><Input placeholder="IP 或主机名" /></Form.Item>
          <Form.Item name="port" label="端口"><InputNumber min={1} max={65535} style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="type" label="类型">
            <Select options={[
              { value: 'linux', label: 'Linux' },
              { value: 'mac', label: 'macOS' },
              { value: 'windows', label: 'Windows' },
            ]} />
          </Form.Item>
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="auth_type" label="认证方式">
            <Select options={[{ value: 'password', label: '密码' }, { value: 'key', label: 'SSH 密钥' }]} />
          </Form.Item>
          <Form.Item noStyle shouldUpdate>
            {({ getFieldValue }) => (
              getFieldValue('auth_type') === 'key' ? (
                <Form.Item name="private_key" label="私钥内容" rules={[{ required: true }]}>
                  <Input.TextArea rows={5} placeholder="粘贴私钥全文（BEGIN OPENSSH PRIVATE KEY...）" />
                </Form.Item>
              ) : (
                <Form.Item name="password" label="密码" rules={[{ required: true }]}>
                  <Input.Password />
                </Form.Item>
              )
            )}
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}