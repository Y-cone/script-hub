import { useEffect, useState, useRef, useCallback } from 'react'
import { Select, Input, Button, Space, message } from 'antd'
import { PlusOutlined, SettingOutlined } from '@ant-design/icons'
import { getTags, createTag } from '../services/api'
import type { TagItem } from '../services/api'
import type { InputRef } from 'antd'

/** 打开标签管理浮层（宿主组件挂 TagManagerPalette 并监听；与 scripthub:tags-changed 同一事件族） */
export const openTagManager = () => window.dispatchEvent(new CustomEvent('scripthub:tags-manage'))

/**
 * 标签多选选择器：选择/新建标签；下拉底部提供「管理标签…」入口（打开 §6.7 管理浮层）。
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
        onClick={openTagManager}
        data-testid="tagpicker-manage"
        block
      >
        管理标签…
      </Button>
    </div>
  )

  return (
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
  )
}