import { useEffect, useMemo } from 'react'
import { Table, Input, Button, Space, Tree, message, Tag, Select } from 'antd'
import { ScanOutlined, SearchOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useScriptStore } from '../stores/scriptStore'
import type { ScriptItem } from '../services/api'
import type { DataNode } from 'antd/es/tree'

const CATEGORY_COLORS: Record<string, string> = {
  python: 'blue',
  shell: 'green',
  bat: 'orange',
  powershell: 'purple',
}

const CATEGORY_OPTIONS = [
  { label: '全部类型', value: '' },
  { label: 'Python', value: 'python' },
  { label: 'Shell', value: 'shell' },
  { label: 'Batch', value: 'bat' },
  { label: 'PowerShell', value: 'powershell' },
]

function buildTree(scripts: ScriptItem[]): DataNode[] {
  const root: Record<string, any> = {}

  for (const s of scripts) {
    const parts = s.relative_path.split(/[/\\]/)
    let node = root
    for (let i = 0; i < parts.length - 1; i++) {
      if (!node[parts[i]]) node[parts[i]] = { __children: {} }
      node = node[parts[i]].__children
    }
  }

  function toTree(obj: Record<string, any>, prefix = ''): DataNode[] {
    return Object.entries(obj).map(([name, val]) => {
      const path = prefix ? `${prefix}/${name}` : name
      return {
        title: name,
        key: `dir:${path}`,
        children: toTree(val.__children || {}, path),
      }
    })
  }

  return toTree(root)
}

export default function ScriptLibrary() {
  const navigate = useNavigate()
  const {
    scripts, total, page, pageSize, loading, search, category,
    setSearch, setCategory, fetchScripts, doScan, setDirectory,
  } = useScriptStore()

  useEffect(() => { fetchScripts() }, [page, search, category])

  const treeData = useMemo(() => buildTree(scripts), [scripts])

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (name: string, record: ScriptItem) => (
        <a onClick={() => navigate(`/scripts/${record.id}`)}>{name}</a>
      ),
    },
    {
      title: '类型',
      dataIndex: 'category',
      key: 'category',
      width: 100,
      render: (cat: string) => <Tag color={CATEGORY_COLORS[cat]}>{cat}</Tag>,
    },
    {
      title: '路径',
      dataIndex: 'relative_path',
      key: 'relative_path',
      ellipsis: true,
    },
  ]

  const handleScan = async () => {
    const result = await doScan()
    message.success(`扫描完成：新增 ${result.added}，更新 ${result.updated}，删除 ${result.removed}`)
  }

  const handleTreeSelect = (keys: React.Key[]) => {
    const key = keys[0]?.toString()
    if (key?.startsWith('dir:')) {
      setDirectory(key.replace('dir:', ''))
    } else {
      setDirectory('')
    }
    fetchScripts()
  }

  return (
    <div style={{ display: 'flex', gap: 16 }}>
      <div style={{ width: 200, borderRight: '1px solid #f0f0f0', paddingRight: 16 }}>
        <Tree
          treeData={[{ title: '全部', key: '__all__', children: treeData }]}
          defaultExpandAll
          onSelect={handleTreeSelect}
        />
      </div>
      <div style={{ flex: 1 }}>
        <Space style={{ marginBottom: 16 }}>
          <Input
            placeholder="搜索脚本..."
            prefix={<SearchOutlined />}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ width: 250 }}
            allowClear
          />
          <Select
            value={category}
            onChange={(val) => {
              setCategory(val)
              useScriptStore.setState({ page: 1 })
            }}
            options={CATEGORY_OPTIONS}
            style={{ width: 130 }}
          />
          <Button icon={<ScanOutlined />} onClick={handleScan} type="primary">
            扫描目录
          </Button>
        </Space>
        <Table
          dataSource={scripts}
          columns={columns}
          rowKey="id"
          loading={loading}
          pagination={{
            current: page,
            pageSize,
            total,
            onChange: (p) => useScriptStore.setState({ page: p }),
          }}
          size="small"
        />
      </div>
    </div>
  )
}
