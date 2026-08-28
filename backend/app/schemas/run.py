from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Dict, Any


class RunRequest(BaseModel):
    script_id: int
    parameters: Optional[Dict[str, Any]] = None
    working_dir: Optional[str] = None
    env_vars: Optional[Dict[str, str]] = None
    timeout: Optional[int] = None
    confirm_dangerous: bool = False


class RunResponse(BaseModel):
    id: int
    status: str
    command: str
    message: str


class RunHistoryOut(BaseModel):
    id: int
    script_id: Optional[int]
    parameters: str
    command: str
    output: str
    output_file: Optional[str]
    exit_code: Optional[int]
    status: str
    duration: Optional[float]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]

    model_config = {"from_attributes": True}


class RunHistoryListOut(BaseModel):
    items: list[RunHistoryOut]
    total: int
    page: int
    page_size: int