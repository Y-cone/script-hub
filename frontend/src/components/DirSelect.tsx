import { useEffect, useState } from 'react'
import { Select, Input, Button, Space } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { getScriptDirs } from '../services/api'

/**
 * 目录下拉选择：可选择已有子目录，也可内嵌新建目录。
 * value = 目录相对路径（'' = 根目录）。
 */
export default function DirSelect({
  value,
  onChange,
}: {
  value?: string
  onChange?: (dir: string) => void
}) {
  const [dirs, setDirs] = useState<string[]>([])
  const [input, setInput] = useState('')

  const load = () => {
    getScriptDirs().then((res) => setDirs(res.data.directories)).catch(() => {})
  }
  useEffect(load, [])

  // 允许用户输入新目录名
  const options = [
    { label: '(根目录)', value: '' },
    ...dirs.map((d) => ({ label: d, value: d })),
  ]
  if (input.trim()) {
    options.push({ label: `新建: ${input.trim()}`, value: input.trim() })
  }

  return (
    <Space.Compact style={{ width: 260 }}>
      <Select
        size="small"
        showSearch
        placeholder="选择或输入新目录"
        value={value}
        onChange={(v) => { if (v !== undefined) onChange?.(v) }}
        options={options}
        onSearch={setInput}
        filterOption={(input, option) =>
          String(option?.value ?? '').toLowerCase().includes(input.toLowerCase())
        }
        style={{ width: '100%' }}
        dropdownRender={(menu) => (
          <>
            {menu}
            <div style={{ display: 'flex', gap: 6, padding: 8, borderTop: '1px solid #f0f0f0' }}>
              <Input
                size="small"
                placeholder="新目录名"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onPressEnter={() => input.trim() && onChange?.(input.trim())}
              />
              <Button
                size="small"
                icon={<PlusOutlined />}
                onClick={() => input.trim() && onChange?.(input.trim())}
              />
            </div>
          </>
        )}
      />
    </Space.Compact>
  )
}