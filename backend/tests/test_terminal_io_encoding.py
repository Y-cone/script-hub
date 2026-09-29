"""交互终端双向编码（③）——离线断言，不建 SSH 会话。

改前：远端输出一律 `data.decode("utf-8", errors="replace")`（Windows 目标的 cp936 回显满屏 U+FFFD）；
      输入一律 `data.encode("utf-8")`（远端 cmd/PowerShell 按自己的代码页读键盘 → 中文变乱码）。
改后：读路径走 ssh_service.RemoteOutputDecoder（批次 AA 的混流解码器）；写路径走
      ssh_service.encode_terminal_input + terminal_input_codec（控制序列逐字节透传，文本按目标代码页）。

本文件直接驱动 RemotePTYSession._read_loop / send（真实被测逻辑），只把 channel 换成假的、用
`__new__` 绕开会真连 SSH 的 _open()。

跑法：cd backend && .venv/bin/python -m pytest tests/test_terminal_io_encoding.py -q
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services.session_manager import RemotePTYSession, SessionHandle   # noqa: E402
from app.services.ssh_service import (                                     # noqa: E402
    RemoteOutputDecoder, encode_terminal_input, terminal_input_codec)

# 方向键（CSI）+ Ctrl-C：终端控制序列按**字节**约定，不能被代码页编码器碰
ARROW = "\x1b[A"
CTRL_C = "\x03"


class _FakeChan:
    """假 channel：按块吐预置字节，记录 sendall 出去的东西。"""

    def __init__(self, chunks: list[bytes]):
        self.chunks = list(chunks)
        self.sent: list[bytes] = []

    def recv(self, _n: int) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""

    def sendall(self, data: bytes):
        self.sent.append(data)


def _session(chunks=(), is_windows=True, shell="cmd"):
    """造一个不建连的 RemotePTYSession：只填读/写路径需要的字段（_open 会真连 SSH，故不走 __init__）。"""
    out: list[str] = []
    s = RemotePTYSession.__new__(RemotePTYSession)
    SessionHandle.__init__(s, "t-io", out.append)
    chan = _FakeChan(list(chunks))
    s.channel = chan
    s.is_windows = is_windows
    s.shell = shell
    s._decoder = RemoteOutputDecoder() if is_windows else None
    s._input_codec = terminal_input_codec(is_windows, shell)
    return s, out, chan


# ── 读路径 ─────────────────────────────────────────────────────────────────────

def test_read_path_decodes_cp936_and_keeps_control_sequences():
    """Windows 目标：cp936 中文可读、无 U+FFFD，`\\x1b[A`/`\\x03` 原样透传；汉字被块边界切开也不丢。"""
    raw = "任务失败".encode("cp936") + b"\r\n" + ARROW.encode() + CTRL_C.encode() + b"ok"
    split = 3                                   # 有意切在「任」的两个字节中间
    s, out, _ = _session([raw[:split], raw[split:]])

    s._read_loop()                              # 真实读线程主体（同步跑一遍即可）

    text = "".join(out)
    assert text == "任务失败\r\n" + ARROW + CTRL_C + "ok\r\n[会话结束]\r\n", repr(text)
    assert "\ufffd" not in text                  # 改前这里是满屏 U+FFFD
    assert "任务失败" in text                    # 改前是 '���ʧ��'
    # 改前的读法（一律 utf-8/replace）确实读不出来 —— 本文件锁的就是这个差异
    assert "\ufffd" in raw.decode("utf-8", errors="replace")


def test_read_path_non_windows_is_plain_utf8_and_controls_survive():
    """非 Windows 远端保持纯 UTF-8 快速路径（不误伤），控制序列同样原样。"""
    s, _, _ = _session(is_windows=False, shell="bash")
    raw = ("中文" + ARROW + CTRL_C).encode("utf-8")
    assert s._decode_output(raw) == "中文" + ARROW + CTRL_C
    # 与改动前的解码逐字节一致（快速路径没被"顺手升级"）
    assert s._decode_output(b"\xff") == b"\xff".decode("utf-8", errors="replace")


# ── 写路径 ─────────────────────────────────────────────────────────────────────

def test_send_to_cp936_console_encodes_text_and_passes_control_bytes():
    """显式 cmd/PowerShell 会话（未动代码页）→ 文本按系统 ANSI(cp936) 发，控制序列逐字节透传。

    若把整串丢进代码页编码器，`\\x1b` 会变成 `?`（cp936 无 U+001B 映射）——那些字节一旦被改写，
    方向键/Ctrl-C/多路复用协议全部失效。
    """
    s, _, chan = _session(is_windows=True, shell="cmd")
    assert s._input_codec == "cp936"

    s.send(ARROW + CTRL_C + "中文")

    assert chan.sent == [ARROW.encode() + CTRL_C.encode() + "中文".encode("cp936")]


def test_send_utf8_passthrough_for_default_shell_and_unix():
    """默认 shell 会话（建连时已 chcp 65001）/ Linux 远端 = UTF-8 直通（旧行为不变）。"""
    for is_win, shell in ((True, ""), (True, "bash"), (False, ""), (False, "bash")):
        s, _, chan = _session(is_windows=is_win, shell=shell)
        assert s._input_codec is None, (is_win, shell)
        s.send(ARROW + CTRL_C + "中文")
        assert chan.sent == [(ARROW + CTRL_C + "中文").encode("utf-8")], (is_win, shell)


def test_input_codec_table():
    """代码页判据表：只有「显式 cmd / PowerShell 的 Windows 会话」才改按 ANSI 编码。"""
    assert terminal_input_codec(False, "cmd") is None
    assert terminal_input_codec(True, "") is None
    assert terminal_input_codec(True, "bash") is None
    assert terminal_input_codec(True, "cmd") == "cp936"
    assert terminal_input_codec(True, "powershell") == "cp936"
    # 纯函数：代码页编码 + 控制透传，不依赖会话对象
    assert encode_terminal_input("\x1b[?1049h", "cp936") == b"\x1b[?1049h"
    assert encode_terminal_input("中文", "cp936") == "中文".encode("cp936")
    assert encode_terminal_input("中文", None) == "中文".encode("utf-8")
