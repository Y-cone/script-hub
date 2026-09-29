"""终端类型白名单自检（批次 N · 新建终端支持选择类型）。

跑法：cd backend && .venv/bin/python tests/test_terminal_shells.py
（批次 AJ 起也由 `pytest tests` 收集执行，见文件末尾 test_main_selfcheck()）
唯一职责：▾ 菜单送来的 shell 名必须只有平台白名单内的值能通过，其余一律
TerminalShellError（WebSocket 路由据此回 error 帧，不静默退回默认）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.executor import SHELL_RUNNERS  # noqa: E402
from app.services.session_manager import (  # noqa: E402
    REMOTE_WIN_BASH_CMD, TERMINAL_SHELLS, RemotePTYSession, WinConPTYSession,
    resolve_terminal_shell,
)


class _FakeWinConPTY(WinConPTYSession):
    """绕过 __init__（不 spawn/不开 pty）：只测 _resolve_shell 的纯字符串映射。"""

    def __init__(self, shell_request: str):
        self.shell_request = shell_request


class _FakeChan:
    """记录 exec_command / invoke_shell 实参的假 SSH channel（读线程立即 EOF）。"""

    def __init__(self):
        self.execd = None

    def get_pty(self, **kw):
        pass

    def exec_command(self, cmd):
        self.execd = cmd

    def invoke_shell(self):
        self.execd = "<invoke_shell 默认>"

    def recv(self, n):
        return b""

    def sendall(self, data):
        pass

    def resize_pty(self, **kw):
        pass

    def close(self):
        pass


class _FakeClient:
    def __init__(self, chan):
        self._chan = chan

    def get_transport(self):
        return self

    def open_session(self):
        return self._chan


def main() -> int:
    # 空/纯空白 = 平台默认（现状不变，走 pty/invoke_shell 既有路径）
    assert resolve_terminal_shell("", False) == ""
    assert resolve_terminal_shell("   ", False) == ""
    assert resolve_terminal_shell("", True) == ""

    # 白名单内 → 原样（归一化大小写/空白）
    assert resolve_terminal_shell("bash", False) == "bash"
    assert resolve_terminal_shell("sh", False) == "sh"
    assert resolve_terminal_shell("zsh", False) == "zsh"
    assert resolve_terminal_shell(" PowerShell ", True) == "powershell"
    assert resolve_terminal_shell("cmd", True) == "cmd"
    assert resolve_terminal_shell("bash", True) == "bash"

    # 跨平台/非法 → 必须拒绝（这是 error 帧而非静默降级的保证）
    bad = [("cmd", False), ("powershell", False), ("zsh", True), ("sh", True),
           ("git-bash", True), ("git-bash", False), ("xxx", False), ("python3", False)]
    for name, is_win in bad:
        try:
            resolve_terminal_shell(name, is_win)
        except ValueError:
            continue
        raise AssertionError(f"未拒绝 {name!r} on {'win32' if is_win else 'unix'}")

    # 漂移护栏：终端类型必须是执行侧白名单（executor.SHELL_RUNNERS）的子集
    for fam in ("unix", "win32"):
        extra = set(TERMINAL_SHELLS[fam]) - set(SHELL_RUNNERS[fam])
        assert not extra, f"{fam}: 终端类型 {extra} 不在执行侧白名单内"

    # Windows 启动名映射：'' / cmd → cmd.exe、powershell → powershell.exe、bash → Git Bash 路径
    def resolve(name: str) -> str:
        return _FakeWinConPTY(name)._resolve_shell()

    assert resolve("") == "cmd.exe"
    assert resolve("cmd") == "cmd.exe"
    assert resolve("powershell") == "powershell.exe"
    assert "bash" in resolve("bash").lower(), resolve("bash")

    # Windows 远端 bash：PATH 注入形式（cmd 默认 PATH 无 Git\bin）
    assert "Program Files\\Git\\bin" in REMOTE_WIN_BASH_CMD
    assert REMOTE_WIN_BASH_CMD.endswith("& bash\"")

    # 远端实际执行命令对照表（假 channel 记录 exec_command 实参；'' → invoke_shell 保持原行为）
    remote = {}

    def remote_cmd(shell: str, is_win: bool) -> str:
        chan = _FakeChan()
        sess = RemotePTYSession("t", lambda _t: None, _FakeClient(chan),
                                cols=80, rows=24, is_windows=is_win, shell=shell)
        sess.close()
        return chan.execd or ""

    assert remote_cmd("", True) == "<invoke_shell 默认>"
    assert remote_cmd("", False) == "<invoke_shell 默认>"
    for name in TERMINAL_SHELLS["unix"]:
        remote[f"unix/{name}"] = remote_cmd(name, False)
        assert remote[f"unix/{name}"] == name
    for name in TERMINAL_SHELLS["win32"]:
        remote[f"win32/{name}"] = remote_cmd(name, True)
        assert remote[f"win32/{name}"] == (REMOTE_WIN_BASH_CMD if name == "bash" else name)

    print("PASS | 终端类型白名单:", {k: list(v) for k, v in TERMINAL_SHELLS.items()})
    print("PASS | Windows 本机启动名:", {n or "''": resolve(n) for n in ("", "cmd", "powershell", "bash")})
    print("PASS | 远端 exec_command:", remote)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def test_main_selfcheck():
    """把原本只走 __main__ 的自检接回 pytest 收集（此前 pytest 从不执行本文件）。
    返回 0 = 全过；失败时 main() 内部直接抛 AssertionError。无副作用（假 channel 桩 +
    绕过 __init__ 的假 session，不真开 pty、不连真实设备）。"""
    assert main() == 0
