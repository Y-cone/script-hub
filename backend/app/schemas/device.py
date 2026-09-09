from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class DeviceOut(BaseModel):
    id: int
    name: str
    type: str
    host: str
    port: int
    auth_type: str
    username: str
    # 不回显凭据
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DeviceCreate(BaseModel):
    name: str
    type: str = "linux"  # linux | mac | windows（仅 UI 区分；执行由 probe.platform 决定）
    host: str
    port: int = 22
    auth_type: str = "password"  # password | key
    username: str
    password: Optional[str] = None  # 仅 auth_type=password 用
    private_key: Optional[str] = None  # 仅 auth_type=key 用（私钥内容）


class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    auth_type: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    private_key: Optional[str] = None


class TestConnResult(BaseModel):
    ok: bool
    message: str
    platform: Optional[str] = None
    os_info: Optional[str] = None