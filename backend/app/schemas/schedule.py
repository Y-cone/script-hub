from pydantic import BaseModel
from datetime import datetime
from typing import Optional


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