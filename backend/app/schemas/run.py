from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional, Dict, Any, Literal


# V5-G（SPEC §2.4）：Shell 覆盖白名单——前端下拉只出平台适用项，此处兜住非法值（422）
ShellName = Literal["bash", "sh", "zsh", "cmd", "powershell", "pwsh", "python3"]


class RunRequest(BaseModel):
    # N1（批次 BM）：未知字段一律 422——`params`/`commands` 这类拼错的键此前被静默忽略，
    # 用户以为传了参数，实际脚本收到的是一份空参数表。字段全部取自前端 RunRequest 接口
    # （frontend/src/services/api.ts），前端发送的键不超出下列集合。
    model_config = ConfigDict(extra="forbid")

    script_id: int
    parameters: Optional[Dict[str, Any]] = None
    working_dir: Optional[str] = None
    env_vars: Optional[Dict[str, str]] = None
    timeout: Optional[int] = None
    confirm_dangerous: bool = False
    confirm_env: bool = False
    device_id: Optional[int] = None  # 目标设备（None=本机执行）
    shell: Optional[ShellName] = None  # 解释器覆盖（None=按脚本类型自动分派）


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
    is_scheduled: Optional[int] = 0
    schedule_id: Optional[int] = None
    device_id: Optional[int] = None
    script_name: Optional[str] = None   # SPEC §7.2-#4：JOIN scripts
    device_name: Optional[str] = None   # JOIN devices

    model_config = {"from_attributes": True}


class RunHistoryListOut(BaseModel):
    items: list[RunHistoryOut]
    total: int
    page: int
    page_size: int