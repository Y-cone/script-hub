import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Card, Descriptions, Button, Spin, Table, Modal, Input, Select, Switch, Space, Popconfirm, message, InputNumber, Tag } from 'antd'
import { ArrowLeftOutlined, PlusOutlined, ThunderboltOutlined, PlayCircleOutlined, StopOutlined, EditOutlined, CheckCircleOutlined, ApartmentOutlined } from '@ant-design/icons'
import { useScriptStore } from '../stores/scriptStore'
import type { ParamDef } from '../services/api'
import { runScript, killRun, updateScript, moveScript, getTags, setScriptTags, saveScriptContent, envCheckScript, getScriptDeps } from '../services/api'
import type { TagItem, DepItem } from '../services/api'
import CodeViewer from '../components/CodeViewer'
import Terminal from '../components/Terminal'
import TagPicker from '../components/TagPicker'
import DirSelect from '../components/DirSelect'
import { getWsBase } from '../config'
import '../styles/danger.css'

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
  const [envRequests, setEnvRequests] = useState('')
  const [isRunning, setIsRunning] = useState(false)
  const [runOutput, setRunOutput] = useState('')
  const [runStatus, setRunStatus] = useState('')
  const [currentRunId, setCurrentRunId] = useState<number | null>(null)
  // 每个参数的实际运行值（区别于默认值）
  const [paramValues, setParamValues] = useState<Record<string, string>>({})
  // 标签
  const [allTags, setAllTags] = useState<TagItem[]>([])
  const [scriptTagIds, setScriptTagIds] = useState<number[]>([])
  // 在线编辑
  const [editMode, setEditMode] = useState(false)
  const [editContent, setEditContent] = useState('')
  const [saving, setSaving] = useState(false)
  // 依赖分析
  const [depsOpen, setDepsOpen] = useState(false)
  const [deps, setDeps] = useState<DepItem[]>([])
  const [depsLoading, setDepsLoading] = useState(false)

  useEffect(() => {
    if (id) {
      fetchScript(Number(id))
      fetchContent(Number(id))
    }
  }, [id, fetchScript, fetchContent])

  useEffect(() => {
    getTags().then((res) => setAllTags(res.data.items)).catch(() => {})
  }, [])

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
      setEnvRequests(currentScript.env_requests || '')
      // 初始化标签（名称 → id）
      if (currentScript.tags?.length && allTags.length) {
        const idList = currentScript.tags
          .map((name) => allTags.find((t) => t.name === name)?.id)
          .filter((x): x is number => x != null)
        setScriptTagIds(idList)
      } else {
        setScriptTagIds([])
      }
    }
  }, [currentScript, allTags])

  // 保存脚本内容（二次确认后）
  const handleSaveContent = useCallback(async () => {
    if (!id) return
    Modal.confirm({
      title: '保存脚本内容',
      content: '保存后将覆盖磁盘文件并重新扫描，确认？',
      okText: '保存',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        setSaving(true)
        try {
          await saveScriptContent(Number(id), editContent)
          // 重新拉取内容（含重扫后的最新状态）
          useScriptStore.getState().fetchContent(Number(id)).then(() => {
            useScriptStore.getState().fetchScript(Number(id))
          })
          // 内容已变，自动重新解析参数
          try {
            const parsed = await useScriptStore.getState().parseParams(Number(id))
            setParams(parsed)
          } catch {
            // 解析失败不阻断保存，保留旧参数
          }
          setEditMode(false)
          message.success('已保存并重新扫描')
        } catch (e: any) {
          message.error(e?.response?.data?.detail || '保存失败')
        } finally {
          setSaving(false)
        }
      },
    })
  }, [id, editContent])

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
    // 前端校验：env_requests / env_vars 必须是合法 JSON 对象
    for (const [, val, label] of [
      ['env_requests', envRequests, '环境要求'],
      ['env_vars', envVars, '环境变量'],
    ]) {
      if (val.trim()) {
        try {
          const parsed = JSON.parse(val)
          if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
            message.error(`${label}必须是 JSON 对象，如 {"python": ">=3.8"}`)
            return
          }
          for (const [k, v] of Object.entries(parsed)) {
            if (typeof v !== 'string') {
              message.error(`${label}的值必须是字符串，键"${k}" 的值为 ${typeof v}`)
              return
            }
          }
        } catch {
          message.error(`${label}不是合法 JSON`)
          return
        }
      }
    }
    try {
      await updateScript(Number(id), {
        working_dir: workingDir || null,
        env_vars: envVars || null,
        timeout,
        env_requests: envRequests || null,
      })
      message.success('配置已保存')
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败')
    }
  }

  // 执行脚本
  const handleRun = async () => {
    if (!id || !currentScript) return
    
    // 高危脚本二次确认
    if (currentScript.dangerous) {
      Modal.confirm({
        title: '⚠️ 高危脚本确认',
        content: (
          <div>
            <p>您即将执行一个<strong>高危脚本</strong>：</p>
            <p style={{ fontFamily: 'monospace', background: '#f5f5f5', padding: 8, borderRadius: 4 }}>
              {currentScript.name}
            </p>
            <p>此脚本可能对系统造成不可逆的影响，请确认您了解脚本的功能。</p>
          </div>
        ),
        okText: '确认执行',
        cancelText: '取消',
        okButtonProps: { danger: true },
        onOk: () => executeScript(),
      })
      return
    }
    
    await executeScript()
  }

  // 实际执行脚本的函数（withEnvConfirm 出现环境告警重试时传 true）
  const executeScript = async (confirmEnv = false) => {
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
        confirm_dangerous: true,
        confirm_env: confirmEnv
      })
      
      setCurrentRunId(result.data.id)
      message.success('脚本已开始执行')
      
      // 连接WebSocket获取实时输出
      connectWebSocket(result.data.id)
      
    } catch (error: any) {
      // 环境检测未达标：弹出确认，用户确认后带 confirm_env 重试
      const status = error?.response?.status
      const detail = error?.response?.data?.detail
      if (status === 409 && detail?.checks) {
        setIsRunning(false)
        setRunStatus('')
        const checks = detail.checks as Array<{ name: string; required: string; actual: string | null; detail: string }>
        Modal.confirm({
          title: '⚠️ 环境检测未达标',
          content: (
            <div>
              {checks.map((c, i) => (
                <p key={i} style={{ margin: '4px 0' }}>
                  <strong>{c.name}</strong>: {c.detail}
                </p>
              ))}
              <p style={{ color: '#888', marginTop: 8 }}>确认继续执行吗？</p>
            </div>
          ),
          okText: '确认执行',
          cancelText: '取消',
          okButtonProps: { danger: true },
          onOk: () => executeScript(true),
        })
        return
      }
      message.error(error?.response?.data?.detail?.message || '执行脚本失败')
      setIsRunning(false)
      setRunStatus('failed')
    }
  }

  // 连接WebSocket获取实时输出
  const connectWebSocket = (runId: number) => {
    const ws = new WebSocket(`${getWsBase()}/api/run/ws/${runId}`)
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

  // 手动环境检测
  const handleEnvCheck = async () => {
    if (!id) return
    try {
      const { data } = await envCheckScript(Number(id))
      Modal.info({
        title: '环境检测结果',
        width: 600,
        content: (
          <div>
            {data.checks.length === 0 && <p style={{ color: '#888' }}>无环境要求</p>}
            {data.checks.map((c, i) => (
              <div
                key={i}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 8,
                  padding: '6px 0',
                  borderBottom: i < data.checks.length - 1 ? '1px solid #f0f0f0' : 'none',
                }}
              >
                <Tag color={c.ok ? 'success' : 'error'}>{c.ok ? '通过' : '未通过'}</Tag>
                <div>
                  <div><strong>{c.name}</strong></div>
                  <div style={{ color: '#666', fontSize: 12 }}>{c.detail}</div>
                </div>
              </div>
            ))}
          </div>
        ),
      })
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '环境检测失败')
    }
  }

  // 手动依赖分析
  const handleDeps = async () => {
    if (!id) return
    setDepsOpen(true)
    setDepsLoading(true)
    try {
      const { data } = await getScriptDeps(Number(id))
      setDeps(data.deps || [])
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '依赖分析失败')
      setDepsOpen(false)
    } finally {
      setDepsLoading(false)
    }
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
    <div style={{ flex: 1, overflow: 'auto' }}>
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
          <Descriptions.Item label="所在目录">
            <DirSelect
              value={currentScript.relative_path.split(/[/\\]/).slice(0, -1).join('/')}
              onChange={async (dir) => {
                try {
                  const { data } = await moveScript(currentScript.id, dir)
                  useScriptStore.setState({ currentScript: data })
                  message.success('已移动到目录 ' + (dir || '(根目录)'))
                } catch (err: any) {
                  message.error(err?.response?.data?.detail || '移动失败')
                }
              }}
            />
          </Descriptions.Item>
          <Descriptions.Item label="危险脚本">
            <Switch
              className="danger-switch"
              checked={currentScript.dangerous}
              onChange={async (checked) => {
                try {
                  await updateScript(currentScript.id, { dangerous: checked })
                  useScriptStore.setState({
                    currentScript: { ...currentScript, dangerous: checked }
                  })
                  message.success(checked ? '已标记为高危脚本' : '已取消高危标记')
                } catch {
                  message.error('修改失败')
                }
              }}
              checkedChildren="是"
              unCheckedChildren="否"
            />
          </Descriptions.Item>
          <Descriptions.Item label="标签" span={2}>
            <TagPicker
              value={scriptTagIds}
              onChange={async (vals) => {
                setScriptTagIds(vals)
                try {
                  await setScriptTags(currentScript.id, vals)
                  const { data } = await getTags()
                  const names = data.items.filter((t) => vals.includes(t.id)).map((t) => t.name)
                  useScriptStore.setState({
                    currentScript: { ...currentScript, tags: names },
                  })
                } catch {
                  message.error('标签更新失败')
                }
              }}
            />
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card
        title="脚本内容"
        style={{ marginBottom: 16 }}
        extra={
          <Space>
            {editMode ? (
              <>
                <Button size="small" onClick={() => { setEditContent(scriptContent ?? ''); setEditMode(false) }}>
                  取消
                </Button>
                <Button
                  size="small"
                  type="primary"
                  loading={saving}
                  onClick={handleSaveContent}
                >
                  保存
                </Button>
              </>
            ) : (
              <Button size="small" icon={<EditOutlined />} onClick={() => { setEditContent(scriptContent ?? ''); setEditMode(true) }}>
                编辑
              </Button>
            )}
          </Space>
        }
      >
        {scriptContent !== null && scriptLanguage ? (
          editMode ? (
            <textarea
              value={editContent}
              onChange={(e) => setEditContent(e.target.value)}
              spellCheck={false}
              style={{
                width: '100%',
                height: 420,
                background: '#1e1e1e',
                color: '#d4d4d4',
                border: '1px solid #333',
                borderRadius: 8,
                padding: 16,
                fontFamily: 'Consolas, "Courier New", monospace',
                fontSize: 13,
                lineHeight: 1.5,
                resize: 'vertical',
              }}
            />
          ) : (
            <CodeViewer code={scriptContent} language={scriptLanguage} />
          )
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
            <label>环境要求（JSON格式，写法见占位符）</label>
            <Input.TextArea
              placeholder='{"python": ">=3.8", "node": ">=18"}'
              value={envRequests}
              onChange={(e) => setEnvRequests(e.target.value)}
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
              <Button icon={<CheckCircleOutlined />} onClick={handleEnvCheck}>环境检测</Button>
              <Button icon={<ApartmentOutlined />} onClick={handleDeps}>依赖分析</Button>
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

      <Modal
        title="依赖分析"
        open={depsOpen}
        onCancel={() => setDepsOpen(false)}
        footer={null}
        width={520}
      >
        <Spin spinning={depsLoading}>
          {deps.length === 0 ? (
            <p style={{ color: '#888' }}>脚本目录下无依赖文件（requirements.txt / package.json / pyproject.toml）</p>
          ) : (
            <Table
              size="small"
              rowKey="name"
              pagination={false}
              dataSource={deps}
              columns={[
                { title: '包名', dataIndex: 'name' },
                { title: '版本约束', dataIndex: 'constraint', width: 120 },
                {
                  title: '状态',
                  key: 'status',
                  width: 140,
                  render: (_: unknown, d: DepItem) =>
                    d.installed ? (
                      <Tag color="success">已装 {d.installed_version}</Tag>
                    ) : (
                      <Tag color="error">未安装</Tag>
                    ),
                },
              ]}
            />
          )}
        </Spin>
      </Modal>
    </div>
  )
}
