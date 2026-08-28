import { useEffect, useMemo, useState } from 'react'
import { Table, Input, Button, Space, Tree, message, Tag, Select, Card, Upload, Modal } from 'antd'
import { ScanOutlined, SearchOutlined, FolderOutlined, UploadOutlined, InboxOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useScriptStore } from '../stores/scriptStore'
import type { ScriptItem } from '../services/api'
import TagPicker from '../components/TagPicker'
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
        icon: <FolderOutlined style={{ color: '#faad14' }} />,
        children: toTree(val.__children || {}, path),
      }
    })
  }

  return toTree(root)
}

export default function ScriptLibrary() {
  const navigate = useNavigate()
  const {
    scripts, total, page, pageSize, loading, search, category, selectedTagIds,
    setSearch, setCategory, setSelectedTagIds, fetchScripts, doScan, doUpload, setDirectory,
  } = useScriptStore()

  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploading, setUploading] = useState(false)

  useEffect(() => { fetchScripts() }, [page, search, category, selectedTagIds])

  const treeData = useMemo(() => buildTree(scripts), [scripts])

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (name: string, record: ScriptItem) => (
        <a onClick={() => navigate(`/scripts/${record.id}`)} style={{ fontWeight: 500 }}>{name}</a>
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
      title: '标签',
      dataIndex: 'tags',
      key: 'tags',
      width: 160,
      render: (tags: string[]) =>
        tags && tags.length ? (
          <Space size={4} wrap>
            {tags.map((t) => (
              <Tag key={t} style={{ borderStyle: 'dashed', borderColor: '#1677ff', color: '#1677ff', background: '#f0f7ff' }}>
                {t}
              </Tag>
            ))}
          </Space>
        ) : <span style={{ color: '#bbb' }}>-</span>,
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

  const handleUpload = async (file: File) => {
    setUploading(true)
    try {
      await doUpload(file)
      message.success(`已上传 ${file.name}`)
      setUploadOpen(false)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '上传失败')
    } finally {
      setUploading(false)
    }
    return false // 阻止 Upload 默认提交
  }

  return (
    <div style={{ display: 'flex', gap: 16, flex: 1, overflow: 'hidden' }}>
      <Card
        size="small"
        style={{ width: 220, flexShrink: 0, overflow: 'auto' }}
        styles={{ body: { padding: '12px 0' } }}
      >
        <Tree
          treeData={[{ title: '全部脚本', key: '__all__', children: treeData }]}
          defaultExpandAll
          onSelect={handleTreeSelect}
          showIcon
        />
      </Card>
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <Space style={{ marginBottom: 16 }} wrap>
          <Input
            placeholder="搜索脚本..."
            prefix={<SearchOutlined />}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ width: 220 }}
            allowClear
          />
          <Select
            value={category}
            onChange={(val) => {
              setCategory(val)
              useScriptStore.setState({ page: 1 })
            }}
            options={CATEGORY_OPTIONS}
            style={{ width: 120 }}
          />
          <TagPicker
            value={selectedTagIds}
            placeholder="按标签筛选 (AND)"
            onChange={(vals) => {
              setSelectedTagIds(vals)
              useScriptStore.setState({ page: 1 })
            }}
          />
          <Button icon={<UploadOutlined />} onClick={() => setUploadOpen(true)}>
            上传脚本
          </Button>
          <Button icon={<ScanOutlined />} onClick={handleScan} type="primary">
            扫描目录
          </Button>
        </Space>
        <Table
          dataSource={scripts}
          columns={columns}
          rowKey="id"
          loading={loading}
          style={{ flex: 1 }}
          pagination={{
            current: page,
            pageSize,
            total,
            showTotal: (t) => `共 ${t} 个脚本`,
            onChange: (p) => useScriptStore.setState({ page: p }),
          }}
          size="small"
          scroll={{ x: 'max-content' }}
        />
      </div>

      <Modal
        title="上传脚本"
        open={uploadOpen}
        onCancel={() => setUploadOpen(false)}
        footer={null}
        width={480}
      >
        <Upload.Dragger
          multiple={false}
          showUploadList={false}
          accept=".py,.sh,.bat,.ps1"
          beforeUpload={handleUpload}
          disabled={uploading}
        >
          <p className="ant-upload-drag-icon">
            {uploading ? <span style={{ color: '#1677ff' }}>上传中...</span> : <InboxOutlined />}
          </p>
          <p className="ant-upload-text">点击或拖拽脚本文件到此处上传</p>
          <p className="ant-upload-hint">支持 .py / .sh / .bat / .ps1，最大 10MB</p>
        </Upload.Dragger>
      </Modal>
    </div>
  )
}