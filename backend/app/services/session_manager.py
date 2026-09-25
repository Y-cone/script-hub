"""终端会话管理器（PRD V4）：会话注册表 + 本机/远程 SessionHandle。

抽象时机（PRD B）：先实作本机(pty)与远程(invoke_shell)两个实现，统一为 SessionHandle
接口（send/resize/recv_callback/close）——不对会话来过度抽象。
V5-C：新增 WinConPTYSession（Windows 本机完整 pty，pywinpty）。
"""
import asyncio
import os
import signal
import struct
import subprocess
import sys
import time
import logging
from pathlib import Path
from threading import Thread
from typing import Callable, Optional

# POSIX pty 专用模块（pty/fcntl/termios）——Windows 上不存在，条件导入（V5-A Windows 打包暴露）
if sys.platform.startswith("win"):
    pty = fcntl = termios = None
else:
    import pty
    import fcntl
    import termios

# pywinpty（V5-C）：Windows 本机 ConPTY，可选依赖，缺失时回退 eval 回显
try:
    import winpty
except ImportError:
    winpty = None

from ..models.device import Device
from ..services.ssh_service import RemoteOutputDecoder, encode_terminal_input, terminal_input_codec

logger = logging.getLogger(__name__)

# 空闲回收默认 30min（PRD B/非功能）
IDLE_TIMEOUT = 30 * 60

# 本机会话默认 shell（类 Unix）
DEFAULT_UNIX_SHELL = "/bin/bash"
# Windows 本机：eval 回显兜底（pywinpty 缺失时用；完整 ConPTY 见 WinConPTYSession）
WIN_EVAL_SHELL = "cmd.exe"

# Git Bash 常见安装目录/可执行文件（本地 winpty 启动需绝对路径——cmd 默认 PATH 不含 Git\bin）；
# 远端 Windows 用等价形式（PATH 注入后调 bash，同 ssh_service.REMOTE_PROBE_CMDS_WIN["bash"] 的知识）
WIN_GIT_BASH_DIRS = (r"C:\Program Files\Git\bin", r"C:\Program Files (x86)\Git\bin")
WIN_GIT_BASH_EXES = tuple(d + r"\bash.exe" for d in WIN_GIT_BASH_DIRS) + (
    r"C:\Program Files\Git\usr\bin\bash.exe",
)
REMOTE_WIN_BASH_CMD = 'cmd /c "set PATH=%PATH%;' + ";".join(WIN_GIT_BASH_DIRS) + '& bash"'

# 显式选终端类型时的首屏清理命令（win32 专用，按 shell 分派）：与默认分支（下方 chcp+cls）
# 同一注入方式——目标 shell 起来后单独 sendall 一条，靠它自己的清屏把横幅/前导换行抹掉。
# cmd / PowerShell：各有版本+版权两行横幅 → cls。Git Bash：无横幅，但 ConPTY 起始那 29 行
# 换行留下一个空行 → clear（不依赖 TERM 的等价写法见注释，实测见批次 O2）。
WIN_SHELL_CLEAR = {"cmd": "cls", "powershell": "cls", "bash": "clear"}

# 交互终端类型白名单（前端 ▾ 菜单契约值）：键与 executor.SHELL_RUNNERS 同源（可用的 shell 名），
# 这里只列「能当交互式 shell 起」的子集（python3/pwsh 是脚本运行器，不是终端类型）。
TERMINAL_SHELLS = {
    "unix": ("bash", "sh", "zsh"),
    "win32": ("cmd", "powershell", "bash"),  # bash = Git Bash（按上面的安装路径解析）
}


class TerminalShellError(ValueError):
    """终端类型非法 / 与目标平台不符（路由层转成 error 帧；不静默退回默认）。"""


def resolve_terminal_shell(shell: str, is_win: bool) -> str:
    """校验并归一化终端类型：'' = 平台默认（现状不变）；非法值抛 TerminalShellError。"""
    name = (shell or "").strip().lower()
    if not name:
        return ""
    kinds = TERMINAL_SHELLS["win32" if is_win else "unix"]
    if name not in kinds:
        raise TerminalShellError(
            f"终端类型「{shell}」不适用于"
            f"{'Windows' if is_win else 'Unix'} 设备（可选：{', '.join(kinds)}；留空=默认）"
        )
    return name


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
        self.read_fd = None  # 读线程私有读 fd（_open 里 dup，见注释）
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
        # slave 号立刻归零：它马上会被 next openpty / dup 复用，close() 里再关一次
        # 会误关别的会话刚拿到的 fd（实测：dup 拿到的正是这个号，close() 把它关掉后
        # 新会话的 openpty 又拿到同号 → 旧读线程照样抢读）
        self.slave_fd = None
        # 读线程私有读 fd（dup）：close() 会关掉 master_fd，该 fd 号随即被下一个 pty.openpty()
        # 复用；读线程若此刻刚启动还没进 os.read（新线程抢 GIL 可等 ~5ms 切换间隔，实测必现），
        # 它读的就是「新会话的 pty」——两个读线程交替各抢 1 字节，新会话回显每两个字符丢一个
        # （批次 R-2 实测：dev/StrictMode 双挂载下 10 键只剩 5 个，bdfhj / acegi 交替出现）。
        # dup 出来的号只属于本读线程，只要没关就复用不到 → 跨会话抢读被根除。
        self.read_fd = os.dup(self.master_fd)
        self._reader = Thread(target=self._read_loop, args=(self.read_fd,), daemon=True)
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

    def _read_loop(self, fd: int):
        """读线程主体：只读 fd 参数（本会话 own 的 dup），不再每次回读 self.master_fd。

        原先每轮 os.read(self.master_fd) 会在 close() 关 fd 后重新解析一个「已被复用的号」
        → 读到别的会话的 pty（跨会话抢字符，R-2 根因）；fd 参数化后目标恒定。
        """
        try:
            while True:
                data = os.read(fd, 4096)
                if not data:
                    break
                self._emit(data.decode("utf-8", errors="replace"))
        except (OSError, ValueError):
            pass
        finally:
            # 读 fd 由读线程自己回收：close() 不能再关（已 dup 出去的号不在主进程 fd 表里被复用）
            try:
                os.close(fd)
            except OSError:
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
        # Windows 本机（master_fd=None）无 pty 尺寸，直接跳过
        if self.closed or self.master_fd is None or fcntl is None:
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
        # killpg 仅 POSIX（Windows 无进程组概念，由下方 terminate 兜底）
        if hasattr(os, "killpg"):
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
        # 归零：close() 后这两个号可能立刻被下一个 openpty 复用，留成 int 会被
        # send/resize 误用（send/resize 另有 self.closed 兜底，这里再堵一层）。
        # 读 fd（self.read_fd）刻意不在这里关——它归读线程所有，见 _read_loop。
        self.master_fd = None
        self.slave_fd = None


class WinConPTYSession(SessionHandle):
    """Windows 本机完整 ConPTY 会话（V5-C，pywinpty）。

    vim/top/方向键/resize 全功能（对比 eval 回显兜底的局限）。
    shell 解析：bash（Git Bash）不在 cmd 默认 PATH——按 PATH + Git Bash
    常见安装路径解析完整可执行路径（与 detect_has_bash 同源知识，路径常量在模块顶部）。
    """

    def __init__(self, session_id: str, output_cb, shell: str = "",
                 cols: int = 80, rows: int = 24):
        super().__init__(session_id, output_cb)
        self.shell_request = shell
        self.cols, self.rows = cols, rows
        self.pty_proc = None
        self._reader: Optional[Thread] = None
        self._open()

    def _resolve_shell(self) -> str:
        """shell 名 → 可执行路径（Windows：bash 需查 Git Bash 安装路径）。"""
        import shutil
        name = (self.shell_request or "").strip().lower()
        if not name or name == "cmd":
            return "cmd.exe"
        if name == "powershell":
            return "powershell.exe"
        if name == "bash":
            for p in WIN_GIT_BASH_EXES:
                if Path(p).exists():
                    return p
            found = shutil.which("bash")
            return found or "bash"
        return self.shell_request  # 用户自定义原样传

    def _open(self):
        if winpty is None:
            raise RuntimeError("pywinpty 未安装（应回退 eval 回显分支，不应到达此处）")
        exe = self._resolve_shell()
        self.pty_proc = winpty.PtyProcess.spawn(
            exe, dimensions=(self.rows, self.cols))
        logger.info(f"ConPTY 会话启动: {exe}")
        self._reader = Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self):
        try:
            while self.pty_proc and self.pty_proc.isalive():
                # pywinpty 阻塞读（read() 返回 str）；无输出时阻塞直至有数据/退出
                data = self.pty_proc.read(4096)
                if not data:
                    break
                self._emit(data)
        except Exception:
            pass
        self._emit("\r\n[会话结束]\r\n")

    def send(self, data: str):
        if self.closed or not self.pty_proc:
            return
        try:
            self.pty_proc.write(data)
        except Exception:
            pass

    def resize(self, cols: int, rows: int):
        if self.closed or not self.pty_proc:
            return
        try:
            self.pty_proc.set_size(rows, cols)
        except Exception:
            pass

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if self.pty_proc and self.pty_proc.isalive():
                self.pty_proc.terminate(force=True)
        except Exception:
            pass


class RemotePTYSession(SessionHandle):
    """远程会话：复用 DeviceSessionPool 连接 + paramiko invoke_shell(get_pty)。

    注意：client 由 registry.create_remote 在 async 上下文先取好再传入，
    避免在 __init__ 里做阻塞连接。
    """

    def __init__(self, session_id: str, output_cb, client,
                 cols: int = 80, rows: int = 24, is_windows: bool = False,
                 shell: str = ""):
        super().__init__(session_id, output_cb)
        self.client = client
        self.is_windows = is_windows
        self.shell = shell
        # Windows 目标：远端回显是 ANSI(cp936) / UTF-8 混流，纯 UTF-8 解码会满屏 U+FFFD
        # → 复用批次 AA 的混合解码器（ssh_service.RemoteOutputDecoder，被真机逼出来的那套）；
        # Linux/macOS 远端保持纯 UTF-8 快速路径（不动现状）。
        self._decoder = RemoteOutputDecoder() if is_windows else None
        # 输入方向编码：Windows 显式 cmd/powershell（未动代码页）要按系统 ANSI 发中文，
        # 其余（含 Linux 远端、Windows 默认 shell 已 chcp 65001、Git Bash）UTF-8 直通。
        self._input_codec = terminal_input_codec(is_windows, shell)
        self.channel = None
        self._reader: Optional[Thread] = None
        self._open(cols, rows)

    def _open(self, cols: int, rows: int):
        chan = self.client.get_transport().open_session()
        chan.get_pty(term="xterm", width=cols, height=rows)
        if self.shell:
            # 指定 shell：exec_command 直接启动目标 shell（带 pty 交互）。
            # 不同于 invoke_shell 后再发命令（会嵌套/报错），exec_command 是替换式，
            # 选 PowerShell/CMD/Bash/Zsh 都能干净进入目标 shell。
            cmd = self.shell
            if self.is_windows and cmd == "bash":
                # Windows：Git Bash 不在默认 PATH → 先并入安装目录再调 bash（本机同源知识）
                cmd = REMOTE_WIN_BASH_CMD
            chan.exec_command(cmd)
            # 显式类型同样要干净首屏（O 批只修了默认分支）：复用同一注入方式，按 shell 分派清屏。
            # 独立一条 sendall，不串 && / &——PowerShell 上 & 是解析错误并留下续行提示符。
            # 只发一个 \r（不用 \r\n）：PowerShell 把后面的 \n 当第二次换行 → 留一个 ">>" 续行提示符；
            # Git Bash 会因此多打一组提示符。清理在建会话时发，早于任何用户输入进 pty，故不吃掉抢跑按键。
            clear = WIN_SHELL_CLEAR.get(self.shell, "") if self.is_windows else ""
            if clear:
                try:
                    chan.sendall(clear + "\r")
                except Exception:
                    pass
        else:
            # 默认 shell：SSH 登录默认（invoke_shell）
            chan.invoke_shell()
            # 仅 Windows 远端（GBK 目标）自动 chcp 65001（PRD F）
            if self.is_windows:
                try:
                    # cls 紧随其后（同一 sendall，按序进输入流）：抹掉 cmd 版本/版权头、
                    # ConPTY 初始化那 29 行换行、以及本行 chcp 的回显——用户看到的首屏只有干净提示符。
                    # 独立两行而非 "& cls"：&&/& 在 PowerShell 默认 shell 上是解析错误并留下续行提示符。
                    chan.sendall("chcp 65001 >nul 2>&1\r\ncls\r\n")
                except Exception:
                    pass
        self.channel = chan
        self._reader = Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _decode_output(self, data: bytes) -> str:
        """远端输出字节 → str。

        Windows 目标：混流（同一行里既有 UTF-8 片段也有 ANSI 片段）→ 复用批次 AA 的
        RemoteOutputDecoder（严格 UTF-8 优先，失败才逐字节混合解码，不丢字节、不静默 replace）。
        feed 按 \\n/\\r 切段；drain 把没有换行的提示符立刻吐出来（否则首屏要等到下一次按键）。
        其他远端：纯 UTF-8 快速路径（与改动前逐字节一致）。
        """
        if self._decoder is None:
            return data.decode("utf-8", errors="replace")
        return self._decoder.feed(data) + self._decoder.drain()

    def _read_loop(self):
        try:
            while True:
                data = self.channel.recv(4096)
                if not data:
                    break
                self._emit(self._decode_output(data))
        except Exception:
            pass
        if self._decoder is not None:
            # 尾半截字节/末行也要出来（flush 不丢尾巴；Linux 远端无缓冲，无副作用）
            self._emit(self._decoder.flush())
        self._emit("\r\n[会话结束]\r\n")

    def send(self, data: str):
        if self.closed or self.channel is None:
            return
        try:
            # 控制序列（方向键/Ctrl-C/ESC）逐字节透传，文本按目标代码页（见 encode_terminal_input）
            self.channel.sendall(encode_terminal_input(data, self._input_codec))
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

    async def create_local(self, session_id: str, output_cb, shell=None,
                           cols=80, rows=24) -> SessionHandle:
        # Windows 本机：优先 ConPTY（V5-C），pywinpty 缺失回退 eval 回显
        if sys.platform.startswith("win") and winpty is not None:
            s = WinConPTYSession(session_id, output_cb, shell or "", cols, rows)
        else:
            s = LocalPTYSession(session_id, output_cb, shell)
        async with self._lock:
            self._sessions[session_id] = s
        return s

    async def create_remote(self, session_id: str, output_cb, device: Device,
                            cols=80, rows=24, shell: str = "",
                            is_windows: bool = False) -> RemotePTYSession:
        """建远程会话。is_windows 由路由层用唯一平台判据（ssh_service.is_win_device）算好传入
        —— 这里不再读 device.type（SPEC：该字段仅 UI 区分，可能标错/过期）。"""
        from ..services.ssh_service import pool
        client = await pool.get(device)
        s = RemotePTYSession(session_id, output_cb, client, cols, rows,
                             is_windows=is_windows, shell=shell)
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