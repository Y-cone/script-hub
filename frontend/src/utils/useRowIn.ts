/**
 * SPEC §3.2.2 #3（v2.38）：列表行数增加时返回一次性 class（sh-rows-in），
 * 由调用方挂到列表容器——整容器淡入重放（取舍：逐行 diff 追踪成本不值，SPEC 报告已声明）。
 * 行数不变/减少/增量 >20 一律不挂。时长由 CSS --dur-list 承担，这里只管挂/摘。
 */
import { useEffect, useRef, useState } from 'react'

export default function useRowIn(rows: number): string {
  const prev = useRef(rows)
  const [on, setOn] = useState(false)
  useEffect(() => {
    const delta = rows - prev.current
    prev.current = rows
    if (delta <= 0 || delta > 20) { setOn(false); return }
    setOn(true)
    const t = setTimeout(() => setOn(false), 180)   // 160ms 动画播完即摘，下次增加可重放
    return () => clearTimeout(t)
  }, [rows])
  return on ? 'sh-rows-in' : ''
}
