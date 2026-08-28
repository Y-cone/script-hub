from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class TagOut(BaseModel):
    id: int
    name: str
    color: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TagCreate(BaseModel):
    name: str
    color: str = "#1677ff"


class TagUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None


class TagListOut(BaseModel):
    items: list[TagOut]
    total: int


class UploadResult(BaseModel):
    added: int
    updated: int
    removed: int
    total: int
    message: str