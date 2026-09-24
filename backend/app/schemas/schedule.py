from pydantic import BaseModel
from datetime import datetime
from typing import Optional

from .brief import LastRunBrief


class ScheduleOut(BaseModel):
    id: int
    script_id: int
    script_name: Optional[str] = None
    script_path: Optional[str] = None
    device_id: Optional[int] = None
    device_name: Optional[str] = None
    name: str
    cron_expr: Optional[str]
    interval_seconds: Optional[int]
    enabled: bool
    parameters: str
    env_vars: Optional[str]
    working_dir: Optional[str]
    timeout: int
    exec_location: Optional[str] = "local"
    next_run_at: Optional[datetime] = None      # SPEC §7.2-#3：下次触发
    last_run: Optional[LastRunBrief] = None     # 上次结果（列表状态点 + 周历事件色）
    delegated: Optional[bool] = None            # ⇗ 下放角标（exec_location='device'）
    device_type: Optional[str] = None           # 下放·crontab / 下放·schtasks 文案区分
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ScheduleCreate(BaseModel):
    script_id: int
    name: str
    cron_expr: Optional[str] = None
    interval_seconds: Optional[int] = None
    parameters: Optional[str] = "{}"
    env_vars: Optional[str] = None
    working_dir: Optional[str] = None
    timeout: Optional[int] = 0
    device_id: Optional[int] = None
    exec_location: Optional[str] = "local"  # local | device（V5-E 调度下放）


class ScheduleUpdate(BaseModel):
    script_id: Optional[int] = None
    name: Optional[str] = None
    cron_expr: Optional[str] = None
    interval_seconds: Optional[int] = None
    enabled: Optional[bool] = None
    parameters: Optional[str] = None
    env_vars: Optional[str] = None
    working_dir: Optional[str] = None
    timeout: Optional[int] = None
    device_id: Optional[int] = None
    exec_location: Optional[str] = None


class ScheduleListOut(BaseModel):
    items: list[ScheduleOut]
    total: int