import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Card, Descriptions, Tag, Button, Spin, Table, Modal, Input, Select, Switch, Space, Popconfirm, message } from 'antd'
import { ArrowLeftOutlined, PlusOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { useScriptStore } from '../stores/scriptStore'
import type { ParamDef } from '../services/api'
import CodeViewer from '../components/CodeViewer'

const CATEGORY_COLORS: Record<string, string> = {
  python: 'blue',
  shell: 'green',
  bat: 'orange',
  powershell: 'purple',
}

const TYPE_LABELS: Record<string, string> = {
  string: 'string',
  int: 'int',
  float: 'float',
  bool: 'bool',
}

function shiftToPrefix(category: string): string {
  switch (category) {
    case 'python': return 'python'
    case 'shell': return 'bash'
    case 'bat': return 'cmd'
    case 'powershell': return 'powershell'
    default: return ''
  }
}

function buildCommand(scriptName: string, category: string, params: ParamDef[]): string {
  const prefix = shiftToPrefix(category)
  const args = params
    .map((p) => (p.type === 'bool' ? p.name : `${p.name} ${p.default || '<value>'}`))
    .join(' ')
  return `${prefix} ${scriptName} ${args}`.trim()
}

export default function ScriptDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { currentScript, scriptContent, scriptLanguage, fetchScript, fetchContent, parseParams, updateParams } = useScriptStore()

  const [params, setParams] = useState<ParamDef[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const [editingIndex, setEditingIndex] = useState<number | null>(null)
  const [form, setForm] = useState<ParamDef>({
    name: '',
    type: 'string',
    default: null,
    required: false,
    description: null,
    choices: null,
  })

  useEffect(() => {
    if (id) {
      fetchScript(Number(id))
      fetchContent(Number(id))
    }
  }, [id, fetchScript, fetchContent])

  useEffect(() => {
    if (currentScript) {
      try {
        setParams(JSON.parse(currentScript.parameters || '[]'))
      } catch {
        setParams([])
      }
    }
  }, [currentScript])

  const handleParse = async () => {
    if (!id) return
    try {
      const parsed = await parseParams(Number(id))
      setParams(parsed)
      message.success(`解析到 ${parsed.length} 个参数`)
    } catch {
      message.error('解析失败')
    }
  }

  const persist = useCallback(async (newParams: ParamDef[]) => {
    if (!id) return
    await updateParams(Number(id), newParams)
  }, [id, updateParams])

  const openAdd = () => {
    setEditingIndex(null)
    setForm({ name: '', type: 'string', default: null, required: false, description: null, choices: null })
    setModalOpen(true)
  }

  const openEdit = (idx: number) => {
    setEditingIndex(idx)
    setForm({ ...params[idx] })
    setModalOpen(true)
  }

  const handleDelete = async (idx: number) => {
    const next = params.filter((_, i) => i !== idx)
    setParams(next)
    await persist(next)
  }

  const handleSave = async () => {
    if (!form.name) {
      message.warning('参数名不能为空')
      return
    }
    let next: ParamDef[]
    if (editingIndex !== null) {
      next = [...params]
      next[editingIndex] = form
    } else {
      next = [...params, form]
    }
    setParams(next)
    setModalOpen(false)
    await persist(next)
  }

  const commandPreview = currentScript
    ? buildCommand(currentScript.name, currentScript.category, params)
    : ''

  if (!currentScript) return <Spin size="large" />

  const columns = [
    { title: '参数名', dataIndex: 'name', key: 'name' },
    { title: '类型', dataIndex: 'type', key: 'type', width: 80 },
    { title: '默认值', dataIndex: 'default', key: 'default',
      render: (v: string | null) => v ?? '-' },
    { title: '必填', dataIndex: 'required', key: 'required', width: 60,
      render: (v: boolean) => v ? '是' : '否' },
    { title: '描述', dataIndex: 'description', key: 'description',
      render: (v: string | null) => v ?? '-' },
    {
      title: '操作', key: 'actions', width: 120,
      render: (_: unknown, _record: ParamDef, idx: number) => (
        <Space>
          <Button size="small" onClick={() => openEdit(idx)}>编辑</Button>
          <Popconfirm title="确认删除?" onConfirm={() => handleDelete(idx)}>
            <Button size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Button
        icon={<ArrowLeftOutlined />}
        onClick={() => navigate('/')}
        style={{ marginBottom: 16 }}
      >
        返回
      </Button>

      <Card title={currentScript.name} style={{ marginBottom: 16 }}>
        <Descriptions column={2} size="small">
          <Descriptions.Item label="路径">{currentScript.path}</Descriptions.Item>
          <Descriptions.Item label="类型">
            <Tag color={CATEGORY_COLORS[currentScript.category]}>{currentScript.category}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="相对路径">{currentScript.relative_path}</Descriptions.Item>
          <Descriptions.Item label="危险脚本">{currentScript.dangerous ? '是' : '否'}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="脚本内容" style={{ marginBottom: 16 }}>
        {scriptContent !== null && scriptLanguage ? (
          <CodeViewer code={scriptContent} language={scriptLanguage} />
        ) : (
          <Spin />
        )}
      </Card>

      <Card
        title="参数配置"
        style={{ marginBottom: 16 }}
        extra={
          <Space>
            <Button icon={<ThunderboltOutlined />} onClick={handleParse}>自动解析</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={openAdd}>手动添加</Button>
          </Space>
        }
      >
        <Table
          dataSource={params.map((p, i) => ({ ...p, key: i }))}
          columns={columns}
          pagination={false}
          size="small"
          locale={{ emptyText: '暂无参数，点击"自动解析"或"手动添加"' }}
        />

        {commandPreview && (
          <div style={{ marginTop: 16, padding: '8px 12px', background: '#1e1e1e', borderRadius: 6, fontFamily: 'monospace', color: '#d4d4d4' }}>
            <span style={{ color: '#888' }}>$ </span>
            {commandPreview}
          </div>
        )}
      </Card>

      <Card title="运行配置">
        <p style={{ color: '#999' }}>Phase 3 实现</p>
      </Card>

      <Modal
        title={editingIndex !== null ? '编辑参数' : '添加参数'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        destroyOnClose
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <label>参数名</label>
            <Input
              placeholder="--target"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </div>
          <div>
            <label>类型</label>
            <Select
              style={{ width: '100%' }}
              value={form.type}
              onChange={(v) => setForm({ ...form, type: v })}
              options={Object.entries(TYPE_LABELS).map(([k, v]) => ({ value: k, label: v }))}
            />
          </div>
          <div>
            <label>默认值</label>
            <Input
              value={form.default ?? ''}
              onChange={(e) => setForm({ ...form, default: e.target.value || null })}
            />
          </div>
          <div>
            <label>必填</label>
            <Switch
              checked={form.required}
              onChange={(v) => setForm({ ...form, required: v })}
            />
          </div>
          <div>
            <label>描述</label>
            <Input
              value={form.description ?? ''}
              onChange={(e) => setForm({ ...form, description: e.target.value || null })}
            />
          </div>
        </div>
      </Modal>
    </div>
  )
}
