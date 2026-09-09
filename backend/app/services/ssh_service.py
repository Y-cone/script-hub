"""SSH 服务：paramiko 连接管理 + 设备级 session 缓存 + 远程执行 + 远端环境探测

架构说明：
- 连接按设备缓存（LRU，会话有效期内复用，减少握手）
- 远程执行输出逐行通过回调送回（由调用方写 DB → WS 轮询推送）
- 本文件不含 DB/业务逻辑，只做 SSH 网络层
"""
import io
import logging
import asyncio
from collections import OrderedDict
from typing import Callable, Optional, Any
import paramiko

from ..models.device import Device
from ..services.secret_store import get_secret

logger = logging.getLogger(__name__)


def _service_name(device_id: int) -> str:
    return f"scripthub-device-{device_id}"


def _load_password(device: Device):
    """读取设备密码（keyring/文件）"""
    return get_secret(_service_name(device.id), device.username)


def _load_key(device: Device):
    """读取设备私钥内容（keyring/文件），解析为 paramiko 私钥对象"""
    key_str = get_secret(_service_name(device.id), device.username)
    if not key_str:
        return None
    for cls in (paramiko.RSAKey, paramiko.ECDSAKey, paramiko.Ed25519Key):
        try:
            return cls.from_private_key(io.StringIO(key_str))
        except (paramiko.SSHException, ValueError, TypeError):
            continue
    return None


def build_client(device: Device, timeout: float = 10.0) -> paramiko.SSHClient:
    """建立 SSH 连接（供测试/执行复用）"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kw = {
        "hostname": device.host,
        "port": device.port,
        "username": device.username,
        "timeout": timeout,
        "banner_timeout": timeout,
        "auth_timeout": timeout,
    }
    if device.auth_type == "key":
        pkey = _load_key(device)
        kw["pkey"] = pkey
        if pkey is None:
            raise paramiko.AuthenticationException("无法读取该设备的私钥凭据")
    else:
        pw = _load_password(device)
        kw["password"] = pw
    client.connect(**kw)
    return client


class DeviceSessionPool:
    """设备级 paramiko 客户端池（LRU，最多 8 个会话）"""

    def __init__(self, maxsize: int = 8):
        self._pool: OrderedDict[int, paramiko.SSHClient] = OrderedDict()
        self._max = maxsize
        self._lock = asyncio.Lock()

    async def get(self, device: Device) -> paramiko.SSHClient:
        """获取设备连接：命中缓存复用，否则新建"""
        async with self._lock:
            client = self._pool.get(device.id)
            if client is not None and client.get_transport() and client.get_transport().is_active():
                self._pool.move_to_end(device.id)
                return client
            # 失效或不存在 → 新建（在事件循环外做阻塞连接）
        client = await asyncio.to_thread(build_client, device)
        async with self._lock:
            self._pool[device.id] = client
            self._pool.move_to_end(device.id)
            if len(self._pool) > self._max:
                _, oldest = self._pool.popitem(last=False)
                try:
                    oldest.close()
                except Exception:
                    pass
        return client

    async def close(self, device_id: int):
        async with self._lock:
            client = self._pool.pop(device_id, None)
        if client:
            try:
                client.close()
            except Exception:
                pass

    async def invalidate(self):
        async with self._lock:
            items = list(self._pool.values())
            self._pool.clear()
        for c in items:
            try:
                c.close()
            except Exception:
                pass


pool = DeviceSessionPool()


async def exec_command(
    device: Device,
    command: str,
    timeout: float = 120,
    output_cb: Optional[Callable[[str], Any]] = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> tuple[int, str]:
    """在远端执行命令。output_cb 每段输出回调（可为异步函数）；返回 (exit_code, 完整输出)"""
    client = await pool.get(device)
    chan = client.get_transport().open_session(timeout=15)
    chan.settimeout(timeout)
    chan.exec_command(command)

    collected = ""
    code = 1

    def _reader_blocking():
        """同步读取 channel 到结束（在 asyncio.to_thread 中跑，避免阻塞事件循环）。
        循环直到 exit_status_ready 且缓冲区读空（无残留输出）。
        带总时限兜底：即使 exit_status_ready 迟迟不触发（如远端半关闭），超时强制收敛。
        """
        nonlocal collected
        import time as _t
        deadline = _t.time() + timeout + 10
        while True:
            if cancel_event and cancel_event.is_set():
                try:
                    chan.close()  # 关闭通道 → 远端进程随会话终止
                except Exception:
                    pass
                break
            # 优先读尽当前缓冲
            if chan.recv_ready() or chan.recv_stderr_ready():
                try:
                    data = chan.recv(4096)
                    if not data:
                        data = chan.recv_stderr(4096)
                except Exception:
                    data = b""
                if data:
                    collected += data.decode("utf-8", errors="replace")
                    continue  # 读到真实数据则继续读
                # 读到的为空（EOF），跳出循环走 exit 检查，避免死循环
            # 缓冲区空且已结束 → 完成
            if chan.exit_status_ready():
                break
            if _t.time() > deadline:
                try:
                    chan.shutdown(1)  # 强制关闭接收，促使 exit_status 收敛
                except Exception:
                    pass
                break
            _t.sleep(0.05)

    # 同步读完整输出（线程中），期间按段回调
    def _run():
        _reader_blocking()
        try:
            return chan.recv_exit_status()
        except Exception:
            return -1

    try:
        code = await asyncio.to_thread(_run)
        # 逐段回调完整输出（模拟实时；小脚本一次性回传足够）
        if output_cb and collected:
            res = output_cb(collected)
            if asyncio.iscoroutine(res):
                await res
    finally:
        try:
            chan.close()
        except Exception:
            pass
    return code, collected


# 远端环境探测命令（复用 envcheck 的探测逻辑，命令走 SSH 在远端执行）
# 分平台：先首探平台，再按平台分发第二段探测命令，避免跨平台误报
REMOTE_PROBE_CMDS_UNIX = {
    "uname": "uname -s",
    "python": "python --version 2>&1",
    "python3": "python3 --version 2>&1",
    "node": "node --version 2>&1",
    "bash": "bash --version 2>&1 | head -1",
    # 仅探测执行脚本所需运行时（.py/.js/.sh）；git/java 与执行路径无关，不探测
}
REMOTE_PROBE_CMDS_WIN = {
    "python": "python --version 2>&1",
    "node": "node --version 2>&1",
    "powershell": "powershell -NoProfile -Command \"$PSVersionTable.PSVersion.ToString()\" 2>&1",
    # 不做 uname/bash/python3（避免 Windows 上误报缺失）；os 信息由 remote_probe 单独取 %OS%
}

# 首探命令：Windows 有 cmd 且 ver 成功→echo WIN；Unix 无 cmd→echo UNX。
# 用退出码+固定 ASCII 标记判平台，避免依赖系统语言/编码（GBK 乱码）解析。
_PLATFORM_PROBE = "cmd /c ver >nul 2>&1 && echo WIN || echo UNX"


async def detect_platform(device: Device) -> str:
    """轻量探测远端平台：仅跑首探命令（含 WIN/UNX 标记），返回 'win32' / 'unix'。"""
    try:
        _, out = await exec_command(device, _PLATFORM_PROBE, timeout=10)
        return "win32" if "WIN" in out.upper() else "unix"
    except Exception:
        return "unix"


def _installed_from_out(first: str, code: int) -> bool:
    """从探测输出判断运行时是否安装（识别 Windows/Linux 的 not found 变体）"""
    if not first or code != 0:
        return False
    low = first.lower()
    nf = ("command not found" in low or "no such file" in low
          or "not recognized" in low or "不是内部或外部命令" in low)
    return not nf


async def remote_probe(device: Device) -> dict:
    """在远端探测平台与运行时版本，返回 envcheck 兼容的结构。

    两段式：首探平台（win32/unix）→ 按平台分发探测命令，避免跨平台误报。
    """
    # 第一段：探测平台（固定 ASCII 标记，避免语言/编码乱码干扰）
    is_win = False
    os_info = ""
    try:
        code, out = await exec_command(device, _PLATFORM_PROBE, timeout=10)
        is_win = "WIN" in out.upper()
    except Exception:
        code = 1

    if is_win:
        # win：os 用 ASCII 的 %OS%（Windows_NT），避免 ver 输出 GBK 乱码
        try:
            _, o = await exec_command(device, "echo %OS%", timeout=10)
            os_info = o.strip().splitlines()[0] if o.strip() else "Windows"
        except Exception:
            os_info = "Windows"
        result = {"platform": "win32", "os_info": os_info, "runtimes": []}
        cmds = REMOTE_PROBE_CMDS_WIN
        order = ("python", "node", "powershell")  # os 已由 %OS% 取得，不再重探 ver（避免 GBK 乱码）
    else:
        result = {"platform": "unix", "os_info": os_info, "runtimes": []}
        cmds = REMOTE_PROBE_CMDS_UNIX
        order = ("uname", "python", "python3", "node", "bash")

    # 第二段：按平台分发探测
    for name in order:
        cmd = cmds[name]
        try:
            c, o = await exec_command(device, cmd, timeout=10)
            first = o.strip().splitlines()[0] if o.strip() else ""
        except Exception as e:
            first = f"探测失败: {e}"
            c = 1
        if name == "uname":
            low = first.lower()
            result["platform"] = "windows" if "microsoft" in low or "windows" in low else "unix"
            result["os_info"] = first
        else:
            result["runtimes"].append(
                {"name": name, "installed": _installed_from_out(first, c), "version": first}
            )
    return result