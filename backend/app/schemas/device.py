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
    # SPEC §7.2-#2：设备卡展示字段（来自最近一次 test/probe 内存缓存 + schedules 聚合）
    online: Optional[bool] = None
    latency_ms: Optional[int] = None
    os_info: Optional[str] = None
    last_error: Optional[str] = None
    # 批次 AK①：最近一次 test/probe 探测到的运行时原文（= remote_probe()["runtimes"]，
    # 与 GET /api/devices/{id}/probe 同结构）。三态（前端据此判 .sh 能否在 Windows 设备上跑）：
    #   None  → 该设备从未探测过（无缓存 / 缓存过期 / 该次探测没拿到运行时）→ 「无结论」
    #   []    → 探过但没拿到任何运行时条目 → 仍是「无结论」，**不等于「没装」**
    #   […]   → 有结论：条目里 installed=False 才是「确定没装」
    # ⚠️ 别用 [] 或缺失表示「没装」——那会让前端把「未知」误判成「确定没有」而标红。
    runtimes: Optional[list[dict]] = None
    # 批次 AO：最近一次 test/probe 探到的平台**原文**（win32/unix/windows；null = 未探测/已过期）。
    # 与 os_info/runtimes 同一份 probe 缓存条目，语义同为「缓存结论」——本机信息页在设备上下文下
    # 直接拿它渲染（不必重探），环境检测端点在后端读同一份缓存判平台兼容性。
    platform: Optional[str] = None
    # 批次 AQ：手里这份结论**有多旧**（毫秒 = now - probe 缓存条目的 ts）。回答「在线/装了 bash
    # 是刚探的还是十分钟前的」——只给**年龄**、不给时间点：缓存 ts 是 ssh_service 的
    # `time.monotonic()`（**不是墙钟**），当时间戳输出就是错的。null ⇔ 没有新鲜结论（与 os_info 同步）。
    probed_age_ms: Optional[int] = None
    schedule_count: int = 0
    delegated_count: int = 0

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
    latency_ms: Optional[int] = None  # SPEC §7.2-#6：连接握手耗时（卡角标 12ms）