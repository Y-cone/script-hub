import { useEffect, useState, useRef, useCallback } from 'react'
import { Select, Input, Button, Space, message, Modal, List, Popconfirm } from 'antd'
import { PlusOutlined, SettingOutlined, DeleteOutlined, EditOutlined } from '@ant-design/icons'
import { getTags, createTag, updateTag, deleteTag } from '../services/api'
import type { TagItem } from '../services/api'
import type { InputRef } from 'antd'

/**
 * 标签多选选择器：选择/新建标签，并提供"管理标签"入口（重命名/删除）。
 */
export default function TagPicker({
  value = [],
  onChange,
  placeholder = '选择标签',
}: {
  value?: number[]
  onChange?: (ids: number[], created: TagItem[]) => void
  placeholder?: string
}) {
  const [tags, setTags] = useState<TagItem[]>([])
  const [search, setSearch] = useState('')
  const [manageOpen, setManageOpen] = useState(false)
  const [editingName, setEditingName] = useState<number | null>(null)
  const [editValue, setEditValue] = useState('')
  const inputRef = useRef<InputRef>(null)

  const load = useCallback(() => {
    getTags().then((res) => setTags(res.data.items)).catch(() => {})
  }, [])
  useEffect(load, [load])

  const handleAdd = async () => {
    const name = search.trim()
    if (!name) return
    try {
      const res = await createTag({ name })
      setTags((prev) => [...prev, res.data])
      const created = res.data
      const nextIds = value.includes(created.id) ? value : [...value, created.id]
      onChange?.(nextIds, [created])
      setSearch('')
      message.success(`已创建标签「${name}」`)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '创建失败')
    }
  }

  const openEdit = (t: TagItem) => {
    setEditingName(t.id)
    setEditValue(t.name)
  }

  const saveEdit = async (t: TagItem) => {
    if (!editValue.trim()) return
    try {
      await updateTag(t.id, { name: editValue.trim() })
      setEditingName(null)
      load()
      message.success('已重命名')
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '重命名失败')
    }
  }

  const handleDelete = async (t: TagItem) => {
    try {
      await deleteTag(t.id)
      load()
      message.success(`已删除标签「${t.name}」`)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '删除失败')
    }
  }

  const renderDropDown = (
    <div style={{ padding: 8 }}>
      <Space.Compact style={{ width: '100%', marginBottom: 6 }}>
        <Input
          ref={inputRef}
          placeholder="新标签名"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onPressEnter={handleAdd}
          size="small"
        />
        <Button icon={<PlusOutlined />} onClick={handleAdd} size="small" />
      </Space.Compact>
      <Button
        type="text"
        size="small"
        icon={<SettingOutlined />}
        onClick={() => setManageOpen(true)}
        block
      >
        管理标签
      </Button>
    </div>
  )

  return (
    <>
      <Select
        mode="multiple"
        style={{ minWidth: 200 }}
        placeholder={placeholder}
        value={value}
        options={tags.map((t) => ({ label: t.name, value: t.id }))}
        onChange={(ids) => onChange?.(ids, [])}
        dropdownRender={(menu) => (
          <>
            {menu}
            {renderDropDown}
          </>
        )}
        maxTagCount="responsive"
        allowClear
      />

      <Modal
        title="管理标签"
        open={manageOpen}
        onCancel={() => setManageOpen(false)}
        footer={null}
        width={400}
      >
        <List
          dataSource={tags}
          locale={{ emptyText: '暂无标签' }}
          renderItem={(t) => (
            <List.Item
              actions={
                editingName === t.id
                  ? [
                    <Button key="ok" size="small" type="primary" onClick={() => saveEdit(t)}>保存</Button>,
                    <Button key="cancel" size="small" onClick={() => setEditingName(null)}>取消</Button>,
                  ]
                  : [
                    <Button key="edit" size="small" icon={<EditOutlined />} onClick={() => openEdit(t)} />,
                    <Popconfirm key="del" title={`删除标签「${t.name}」？`} onConfirm={() => handleDelete(t)}>
                      <Button size="small" danger icon={<DeleteOutlined />} />
                    </Popconfirm>,
                  ]
              }
            >
              {editingName === t.id ? (
                <Input
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  onPressEnter={() => saveEdit(t)}
                  size="small"
                />
              ) : (
                t.name
              )}
            </List.Item>
          )}
        />
      </Modal>
    </>
  )
}