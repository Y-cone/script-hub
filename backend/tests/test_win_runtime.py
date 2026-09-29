"""批次 BM ①（K2）：本机 Python 解释器解析必须滤掉 Windows Store 的 python3 假垫片。

真机现象：Windows 上 `shutil.which("python3")` 命中 %LOCALAPPDATA%\\Microsoft\\WindowsApps\\
python3.EXE（Store 的 App Execution Alias），跑起来只打印「未安装」提示、exit 9009 → 本机所有
.py 执行全失败。resolve_python() 要靠**实跑探测**分辨真假，全假时回退 sys.executable。

跑法：cd backend && .venv/bin/python -m pytest tests/test_win_runtime.py -q
（subprocess.run 被 mock：不真的起进程，也不依赖测试机上装没装 python3）
"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import win_runtime  # noqa: E402

SHIM = r"C:\Users\u\AppData\Local\Microsoft\WindowsApps\python3.EXE"
REAL = r"C:\Python312\python.exe"
SHIM_TEXT = ("Python was not found; run without arguments to install from the Microsoft Store, "
             "or disable this shortcut from Settings > Manage App Execution Aliases.")


class _Result:
    def __init__(self, stdout: bytes, returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


@pytest.fixture(autouse=True)
def _fresh_cache():
    win_runtime.resolve_python.cache_clear()
    yield
    win_runtime.resolve_python.cache_clear()


def test_store_shim_skipped_real_python_win(monkeypatch):
    """python3 = 商店垫片（打印提示 + 9009）→ 跳过；python = 真身 → 选中 python。"""
    monkeypatch.setattr(win_runtime.shutil, "which",
                        lambda name: {"python3": SHIM, "python": REAL}.get(name))

    def fake_run(cmd, **kw):
        assert kw.get("timeout") == 10 and kw.get("capture_output") is True, kw
        if cmd[0] == SHIM:
            return _Result(SHIM_TEXT.encode(), 9009)
        return _Result(b"3\n", 0)

    monkeypatch.setattr(win_runtime.subprocess, "run", fake_run)
    assert win_runtime.resolve_python() == REAL


def test_probe_timeout_also_treated_as_shim(monkeypatch):
    """垫片也可能挂住（超时）→ 同样算假，继续找下一个候选。"""
    monkeypatch.setattr(win_runtime.shutil, "which", lambda name: SHIM if name == "python3" else REAL)

    def fake_run(cmd, **kw):
        if cmd[0] == SHIM:
            raise subprocess.TimeoutExpired(cmd, 10)
        return _Result(b"3\n", 0)

    monkeypatch.setattr(win_runtime.subprocess, "run", fake_run)
    assert win_runtime.resolve_python() == REAL


def test_no_real_python_falls_back_to_sys_executable(monkeypatch):
    """全假（或 PATH 里根本没有）→ 回退 sys.executable：sidecar 自己的解释器总能跑，不至于无从执行。"""
    monkeypatch.setattr(win_runtime.shutil, "which", lambda name: SHIM)
    monkeypatch.setattr(win_runtime.subprocess, "run",
                        lambda cmd, **kw: _Result(SHIM_TEXT.encode(), 9009))
    assert win_runtime.resolve_python() == sys.executable

    win_runtime.resolve_python.cache_clear()
    monkeypatch.setattr(win_runtime.shutil, "which", lambda name: None)
    assert win_runtime.resolve_python() == sys.executable


def test_result_is_cached(monkeypatch):
    """缓存：进程生命周期内只探测一次（每次执行都探 = 每个脚本多两次子进程开销）。"""
    calls = []
    monkeypatch.setattr(win_runtime.shutil, "which", lambda name: REAL if name == "python3" else None)

    def fake_run(cmd, **kw):
        calls.append(cmd[0])
        return _Result(b"3\n", 0)

    monkeypatch.setattr(win_runtime.subprocess, "run", fake_run)
    assert win_runtime.resolve_python() == REAL
    assert win_runtime.resolve_python() == REAL
    assert calls == [REAL], calls


def test_build_command_uses_resolve_python(monkeypatch):
    """接线检查：本机 python 类脚本走 resolve_python()（旧 which 逻辑已无残留）。"""
    from app.models.script import Script
    from app.services import executor

    monkeypatch.setattr(executor, "resolve_python", lambda: REAL)
    s = Script(id=1, name="a.py", path="/srv/脚本 目录/a.py", relative_path="a.py",
               extension=".py", category="python")
    cmd = executor.ScriptExecutor()._build_command(s, {})
    assert cmd.startswith(f'"{REAL}"'), cmd
    assert cmd.count('"') == 4, cmd          # 解释器与路径都带引号（路径含空格）
