"""终端会话管理器（PRD V4）：会话注册表 + 本机/远程 SessionHandle。

抽象时机（PRD B）：先实作本机(pty)与远程(invoke_shell)两个实现，统一为 SessionHandle
接口（send/resize/recv_callback/close）——不对会话来过度抽象。
"""
import asyncio
import os
import pty
import fcntl
import termios
import signal
import struct
import subprocess
import sys
import time
import logging
from threading import Thread
from typing import Callable, Optional

from ..models.device import Device

logger = logging.getLogger(__name__)

# 空闲回收默认 30min（PRD B/非功能）
IDLE_TIMEOUT = 30 * 60

# 本机会话默认 shell（类 Unix）
DEFAULT_UNIX_SHELL = "/bin/bash"
# Windows 本机：最小 eval 式回显（不引入 pywinpty，完整 ConPTY 留 V5）
WIN_EVAL_SHELL = "cmd.exe"


class SessionHandle:
    """统一会话接口：send/resize/recv_callback/close。

    本机与远程是不同实现，对上层同一协议。
    """

    def __init__(self, session_id: str, output_cb: Callable[[str], None]):
        self.session_id = session_id
        self.output_cb = output_cb  # 回调：接收到的 shell 输出
        self.last_active = time.time()
        self.closed = False

    def touch(self):
        self.last_active = time.time()

    @property
    def idle_seconds(self) -> float:
        return time.time() - self.last_active

    def send(self, data: str):
        raise NotImplementedError

    def resize(self, cols: int, rows: int):
        raise NotImplementedError

    def _emit(self, text: str):
        self.touch()
        try:
            self.output_cb(text)
        except Exception:
            pass

    def close(self):
        raise NotImplementedError


class LocalPTYSession(SessionHandle):
    """本机会话：pty.openpty() + subprocess.Popen，POSIX pty 驱动。"""

    def __init__(self, session_id: str, output_cb, shell: Optional[str] = None):
        super().__init__(session_id, output_cb)
        self.shell = shell or DEFAULT_UNIX_SHELL
        self.master_fd = None
        self.slave_fd = None
        self.proc: Optional[subprocess.Popen] = None
        self._reader: Optional[Thread] = None
        self._open()

    def _open(self):
        if sys.platform.startswith("win"):
            # Windows 本机：最小 eval 回显（无 pty；Popen cmd + stdin/stdout 管道回显）。
            # (ponytail: 逐行回显实现；完整 ConPTY 留 V5 桌面化 Tauri)
            self.proc = subprocess.Popen(
                [WIN_EVAL_SHELL],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            self._reader = Thread(target=self._win_read_loop, daemon=True)
            self._reader.start()
            return
        self.master_fd, self.slave_fd = pty.openpty()
        self.proc = subprocess.Popen(
            [self.shell],
            stdin=self.slave_fd, stdout=self.slave_fd, stderr=self.slave_fd,
            preexec_fn=os.setsid, close_fds=True,
        )
        os.close(self.slave_fd)
        self._reader = Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _win_read_loop(self):
        """Windows 本机：读 cmd 输出回显。"""
        try:
            while self.proc and self.proc.stdout:
                data = self.proc.stdout.readline()
                if not data:
                    break
                self._emit(data.decode("utf-8", errors="replace"))
        except Exception:
            pass
        self._emit("\r\n[会话结束]\r\n")

    def _read_loop(self):
        try:
            while True:
                data = os.read(self.master_fd, 4096)
                if not data:
                    break
                self._emit(data.decode("utf-8", errors="replace"))
        except (OSError, ValueError):
            pass
        # EOF → emit exit 标记
        self._emit("\r\n[会话结束]\r\n")

    def send(self, data: str):
        if self.closed:
            return
        if self.master_fd is not None:
            try:
                os.write(self.master_fd, data.encode("utf-8"))
            except OSError:
                pass
        elif self.proc and self.proc.stdin:
            # Windows 本机 eval：写 cmd stdin
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except Exception:
                pass

    def resize(self, cols: int, rows: int):
        if self.closed or self.master_fd is None:
            return
        try:
            win = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, win)
        except OSError:
            pass

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if self.proc and self.proc.poll() is None:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass
        for fd in (self.master_fd, self.slave_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass


class RemotePTYSession(SessionHandle):
    """远程会话：复用 DeviceSessionPool 连接 + paramiko invoke_shell(get_pty)。

    注意：client 由 registry.create_remote 在 async 上下文先取好再传入，
    避免在 __init__ 里做阻塞连接。
    """

    def __init__(self, session_id: str, output_cb, client,
                 cols: int = 80, rows: int = 24):
        super().__init__(session_id, output_cb)
        self.client = client
        self.channel = None
        self._reader: Optional[Thread] = None
        self._open(cols, rows)

    def _open(self, cols: int, rows: int):
        self.channel = self.client.get_transport().open_session()
        self.channel.get_pty(term="xterm", width=cols, height=rows)
        self.channel.invoke_shell()
        # send 专用（invoke_shell 后用 write 或 send，paramiko channel.send 即可）
        # Windows GBK 目标：会话建立后自动 chcp 65001（PRD F）
        try:
            self.channel.sendall("chcp 65001 >nul 2>&1\r\n")
        except Exception:
            pass
        self._reader = Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self):
        try:
            while True:
                data = self.channel.recv(4096)
                if not data:
                    break
                self._emit(data.decode("utf-8", errors="replace"))
        except Exception:
            pass
        self._emit("\r\n[会话结束]\r\n")

    def send(self, data: str):
        if self.closed or self.channel is None:
            return
        try:
            self.channel.sendall(data.encode("utf-8"))
        except Exception:
            pass

    def resize(self, cols: int, rows: int):
        if self.closed or self.channel is None:
            return
        try:
            self.channel.resize_pty(width=cols, height=rows)
        except Exception:
            pass

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if self.channel:
                self.channel.close()
        except Exception:
            pass


class SessionRegistry:
    """会话注册表：全局 session_id→handle；空闭/超时回收。"""

    def __init__(self):
        self._sessions: dict[str, SessionHandle] = {}
        self._lock = asyncio.Lock()

    async def create_local(self, session_id: str, output_cb, shell=None) -> LocalPTYSession:
        s = LocalPTYSession(session_id, output_cb, shell)
        async with self._lock:
            self._sessions[session_id] = s
        return s

    async def create_remote(self, session_id: str, output_cb, device: Device,
                            cols=80, rows=24) -> RemotePTYSession:
        from ..services.ssh_service import pool
        client = await pool.get(device)
        s = RemotePTYSession(session_id, output_cb, client, cols, rows)
        async with self._lock:
            self._sessions[session_id] = s
        return s

    def get(self, session_id: str) -> Optional[SessionHandle]:
        return self._sessions.get(session_id)

    async def remove(self, session_id: str):
        async with self._lock:
            s = self._sessions.pop(session_id, None)
        if s:
            try:
                s.close()
            except Exception:
                pass

    async def close_all(self):
        async with self._lock:
            items = list(self._sessions.values())
            self._sessions.clear()
        for s in items:
            try:
                s.close()
            except Exception:
                pass

    async def reap_idle(self, timeout: float = IDLE_TIMEOUT):
        """回收空闲超时会话"""
        now = time.time()
        victims = [(sid, s) for sid, s in self._sessions.items() if now - s.last_active > timeout]
        for sid, s in victims:
            logger.info(f"回收空闲会话 {sid}（空闲 {s.idle_seconds:.0f}s）")
            await self.remove(sid)


registry = SessionRegistry()