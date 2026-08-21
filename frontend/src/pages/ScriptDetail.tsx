import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Card, Descriptions, Tag, Button, Spin, Table, Modal, Input, Select, Switch, Space, Popconfirm, message, Form, InputNumber } from 'antd'
import { ArrowLeftOutlined, PlusOutlined, ThunderboltOutlined, PlayCircleOutlined, StopOutlined } from '@ant-design/icons'
import { useScriptStore } from '../stores/scriptStore'
import type { ParamDef } from '../services/api'
import { runScript, killRun, updateScript } from '../services/api'
import CodeViewer from '../components/CodeViewer'
import Terminal from '../components/Terminal'

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

  // 运行配置状态（从脚本DB记录加载）
  const [workingDir, setWorkingDir] = useState('')
  const [envVars, setEnvVars] = useState('')
  const [timeout, setTimeout] = useState(0)
  const [isRunning, setIsRunning] = useState(false)
  const [runOutput, setRunOutput] = useState('')
  const [runStatus, setRunStatus] = useState('')
  const [currentRunId, setCurrentRunId] = useState<number | null>(null)
  // 每个参数的实际运行值（区别于默认值）
  const [paramValues, setParamValues] = useState<Record<string, string>>({})

  useEffect(() => {
    if (id) {
      fetchScript(Number(id))
      fetchContent(Number(id))
    }
  }, [id, fetchScript, fetchContent])

  useEffect(() => {
    if (currentScript) {
      try {
        const parsed = JSON.parse(currentScript.parameters || '[]')
        setParams(parsed)
        // 初始化参数运行值为默认值
        const vals: Record<string, string> = {}
        parsed.forEach((p: ParamDef) => { vals[p.name] = p.default ?? '' })
        setParamValues(vals)
      } catch {
        setParams([])
        setParamValues({})
      }
      // 从DB加载运行配置
      setWorkingDir(currentScript.working_dir || '')
      setEnvVars(currentScript.env_vars || '')
      setTimeout(currentScript.timeout || 0)
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

  // 保存运行配置到数据库
  const saveRunConfig = async () => {
    if (!id) return
    try {
      await updateScript(Number(id), { working_dir: workingDir || null, env_vars: envVars || null, timeout })
      message.success('配置已保存')
    } catch {
      message.error('保存失败')
    }
  }

  // 执行脚本
  const handleRun = async () => {
    if (!id || !currentScript) return
    
    try {
      setIsRunning(true)
      setRunOutput('')
      setRunStatus('running')
      
      // 构建参数对象（使用运行时输入值，不是默认值）
      const parameters: Record<string, any> = {}
      params.forEach(p => {
        const val = paramValues[p.name] ?? p.default ?? ''
        if (p.type === 'bool') {
          parameters[p.name] = val === 'true' || val === '1'
        } else {
          if (val !== '') parameters[p.name] = val
        }
      })
      
      // 解析环境变量
      let envVarsObj: Record<string, string> | undefined
      if (envVars) {
        try {
          envVarsObj = JSON.parse(envVars)
        } catch {
          message.error('环境变量格式错误，请使用JSON格式')
          setIsRunning(false)
          return
        }
      }
      
      // 调用执行API
      const result = await runScript({
        script_id: Number(id),
        parameters,
        working_dir: workingDir || undefined,
        env_vars: envVarsObj,
        timeout: timeout || undefined,
        confirm_dangerous: true
      })
      
      setCurrentRunId(result.data.id)
      message.success('脚本已开始执行')
      
      // 连接WebSocket获取实时输出
      connectWebSocket(result.data.id)
      
    } catch (error) {
      message.error('执行脚本失败')
      setIsRunning(false)
      setRunStatus('failed')
    }
  }

  // 连接WebSocket获取实时输出
  const connectWebSocket = (runId: number) => {
    const ws = new WebSocket(`ws://${window.location.host}/api/run/ws/${runId}`)
    let lastOutputLen = 0  // 追踪已显示的输出长度
    
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)
      // 后端每次发送完整输出，只追加新增部分
      const fullOutput = data.output || ''
      if (fullOutput.length > lastOutputLen) {
        setRunOutput(fullOutput)
        lastOutputLen = fullOutput.length
      }
      if (data.status) {
        setRunStatus(data.status)
        if (data.status !== 'running') {
          setIsRunning(false)
          ws.close()
        }
      }
    }
    
    ws.onerror = () => {
      message.error('WebSocket连接错误')
      setIsRunning(false)
      setRunStatus('failed')
    }
    
    ws.onclose = () => {
      if (isRunning) {
        setIsRunning(false)
      }
    }
  }

  // 终止脚本
  const handleKill = async () => {
    if (!currentRunId) return
    
    try {
      await killRun(currentRunId)
      message.success('脚本已终止')
      setIsRunning(false)
      setRunStatus('killed')
      
    } catch (error) {
      message.error('终止脚本失败')
    }
  }

  // 清空输出
  const handleClearOutput = () => {
    setRunOutput('')
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

      <Card title="运行配置" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* 参数输入 */}
          {params.length > 0 && (
            <div>
              <label style={{ fontWeight: 'bold', marginBottom: 8, display: 'block' }}>脚本参数</label>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {params.map((p) => (
                  <div key={p.name} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ minWidth: 120, fontFamily: 'monospace', fontSize: 13 }}>{p.name}</span>
                    {p.type === 'bool' ? (
                      <Switch
                        checked={paramValues[p.name] === 'true'}
                        onChange={(v) => setParamValues(prev => ({ ...prev, [p.name]: v ? 'true' : 'false' }))}
                      />
                    ) : (
                      <Input
                        value={paramValues[p.name] ?? ''}
                        onChange={(e) => setParamValues(prev => ({ ...prev, [p.name]: e.target.value }))}
                        placeholder={p.default || p.description || ''}
                        style={{ flex: 1 }}
                      />
                    )}
                    {p.description && <span style={{ color: '#999', fontSize: 12 }}>{p.description}</span>}
                  </div>
                ))}
              </div>
            </div>
          )}
          <div>
            <label>工作目录</label>
            <Input
              placeholder="留空则使用脚本所在目录"
              value={workingDir}
              onChange={(e) => setWorkingDir(e.target.value)}
            />
          </div>
          <div>
            <label>环境变量（JSON格式）</label>
            <Input.TextArea
              placeholder='{"KEY": "value"}'
              value={envVars}
              onChange={(e) => setEnvVars(e.target.value)}
              rows={3}
            />
          </div>
          <div>
            <label>超时时间（秒，0表示不限）</label>
            <InputNumber
              min={0}
              value={timeout}
              onChange={(v) => setTimeout(v || 0)}
              style={{ width: '100%' }}
            />
          </div>
          <div>
            <Space>
              <Button
                type="primary"
                icon={<PlayCircleOutlined />}
                onClick={handleRun}
                loading={isRunning}
                disabled={isRunning}
              >
                执行脚本
              </Button>
              {isRunning && (
                <Button
                  danger
                  icon={<StopOutlined />}
                  onClick={handleKill}
                >
                  终止
                </Button>
              )}
              <Button onClick={saveRunConfig}>保存配置</Button>
            </Space>
          </div>
        </div>
      </Card>

      <Card title="执行输出">
        <Terminal
          output={runOutput}
          status={runStatus}
          onClear={handleClearOutput}
        />
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
