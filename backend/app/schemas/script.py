from pydantic import BaseModel
from datetime import datetime
from typing import Optional

from .brief import LastRunBrief


class ScriptOut(BaseModel):
    id: int
    name: str
    path: str
    relative_path: str
    extension: str
    category: str
    description: str
    parameters: str
    working_dir: Optional[str]
    env_vars: Optional[str]
    dangerous: bool
    timeout: int
    source: str
    env_requests: Optional[str] = None
    dependencies: Optional[str] = None
    available: Optional[bool] = None
    tags: list[str] = []
    created_at: datetime
    updated_at: datetime
    last_run: Optional[LastRunBrief] = None  # SPEC §7.2-#1：列表「最近运行」列 + 状态点

    model_config = {"from_attributes": True}


class ScriptUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    parameters: Optional[str] = None
    working_dir: Optional[str] = None
    env_vars: Optional[str] = None
    dangerous: Optional[bool] = None
    timeout: Optional[int] = None
    env_requests: Optional[str] = None
    dependencies: Optional[str] = None


class MoveRequest(BaseModel):
    """移动到目标子目录（相对脚本根目录）"""
    directory: str


class ScriptListOut(BaseModel):
    items: list[ScriptOut]
    total: int
    page: int
    page_size: int


class ScanResult(BaseModel):
    added: int
    updated: int
    removed: int
    total: int


class TagsUpdate(BaseModel):
    tag_ids: list[int] = []