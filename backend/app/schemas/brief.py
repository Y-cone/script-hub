"""V5-G 接口契约共享模型（docs/V5G-UI-SPEC.md §7.2）

字段命名与 frontend/src/services/api.ts 逐字一致（§7.8 验收项）。
"""
from typing import Optional

from pydantic import BaseModel


class LastRunBrief(BaseModel):
    """脚本/调度最近一次运行摘要（列表接口轻量携带）"""
    status: str
    exit_code: Optional[int] = None
    started_at: Optional[str] = None  # ISO 字符串（datetime 在 JSON 序列化层已转 str）
    duration: Optional[float] = None  # 秒
