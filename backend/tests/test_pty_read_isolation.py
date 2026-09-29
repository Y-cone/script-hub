"""回归测试（批次 R-2）：pty 会话更替时读线程不得抢读新会话的 pty。

根因（实测复现）：LocalPTYSession.close() 关掉 master_fd 后，fd 号立刻被下一个
pty.openpty() 复用；若旧会话的读线程此刻还没进 os.read（新线程抢 GIL 可被拖到毫秒级，
dev 的 StrictMode 双挂载下必现），它的 os.read(self.master_fd) 就落在了新会话的 pty 上
→ 两个读线程交替各抢 1 字节 → 前端看到的回显"每两个字符丢一个"（实测 bdfhj / acegi）。

修法：读线程持私有 dup 出来的 fd（self.read_fd），close() 不再关它、改由读线程退出时回收。
本测试用 gate 强制制造"旧读线程晚于新会话 openpty 才进 read"的时序。
"""
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"), reason="POSIX pty 专用")

from app.services import session_manager as sm  # noqa: E402

KEYS = "0123456789"


def test_stale_reader_must_not_steal_new_session_pty():
    gate = threading.Event()
    orig_read_loop = sm.LocalPTYSession._read_loop

    def delayed(self, fd: int):
        # 卡住旧会话的读线程，直到新会话已经建好（= 旧 fd 号已被复用）
        gate.wait(3.0)
        return orig_read_loop(self, fd)

    sm.LocalPTYSession._read_loop = delayed
    try:
        bucket_a: list[str] = []
        bucket_b: list[str] = []
        a = sm.LocalPTYSession("test-a", bucket_a.append)
        a.close()  # 立刻关：读线程还卡在 gate 上，fd 号随即空出
        b = sm.LocalPTYSession("test-b", bucket_b.append)
        assert b.master_fd is not None
        gate.set()  # 放行 a 的陈旧读线程
        time.sleep(0.3)

        for ch in KEYS:
            b.send(ch)
            time.sleep(0.02)
        time.sleep(0.5)

        got = "".join(bucket_b)
        assert got.endswith(KEYS), f"新会话回显被抢读，丢失字符: {got!r}"
        assert not any(ch in "".join(bucket_a) for ch in KEYS), "旧会话读线程读到了新会话的 pty"
        b.close()
    finally:
        sm.LocalPTYSession._read_loop = orig_read_loop


def test_single_session_roundtrip_intact():
    bucket: list[str] = []
    s = sm.LocalPTYSession("test-solo", bucket.append)
    time.sleep(0.5)
    for ch in KEYS:
        s.send(ch)
        time.sleep(0.01)
    time.sleep(0.5)
    assert "".join(bucket).endswith(KEYS)
    s.close()
