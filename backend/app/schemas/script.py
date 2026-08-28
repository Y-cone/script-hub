from pydantic import BaseModel
from datetime import datetime
from typing import Optional


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
    tags: list[str] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ScriptUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    parameters: Optional[str] = None
    working_dir: Optional[str] = None
    env_vars: Optional[str] = None
    dangerous: Optional[bool] = None
    timeout: Optional[int] = None


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