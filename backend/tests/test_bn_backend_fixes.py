"""批次 BN 后端确定性小修回归：resize(N7) / 探活(K3) / 高危扫描(N2) / 孤儿凭据(N10) / 迁移(N9)。

跑法：cd backend && .venv/bin/python -m pytest tests/test_bn_backend_fixes.py -q
"""
import asyncio
import logging
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


# ── N7：ConPTY resize ────────────────────────────────────────────────────────
def test_winconpty_resize_calls_setwinsize():
    """resize(cols=120, rows=40) → setwinsize(rows=40, cols=120)（pywinpty 参数序）。"""
    from app.services.session_manager import WinConPTYSession

    calls = []

    class FakePty:
        def setwinsize(self, rows, cols):
            calls.append(("setwinsize", rows, cols))

        def set_size(self, rows, cols):  # 旧 API 名：绝不应再被调
            calls.append(("set_size", rows, cols))

    s = object.__new__(WinConPTYSession)  # 跳过 __init__（不真起 pty）
    s.session_id = "t"
    s.output_cb = lambda _: None
    s.closed = False
    s.pty_proc = FakePty()

    s.resize(120, 40)
    assert calls == [("setwinsize", 40, 120)]


def test_winconpty_resize_failure_logs_warning(caplog):
    """setwinsize 抛异常 → logger.warning 带异常文本（不许静默吞）。"""
    from app.services.session_manager import WinConPTYSession

    class BoomPty:
        def setwinsize(self, rows, cols):
            raise AttributeError("boom-detail")

    s = object.__new__(WinConPTYSession)
    s.session_id = "t"
    s.output_cb = lambda _: None
    s.closed = False
    s.pty_proc = BoomPty()

    with caplog.at_level(logging.WARNING, logger="app.services.session_manager"):
        s.resize(80, 24)  # 不应抛
    assert any("boom-detail" in r.message for r in caplog.records)


# ── K3：探活输出校验 ─────────────────────────────────────────────────────────
def test_probe_versions_rejects_store_shim_text(monkeypatch):
    """垫片输出整段商店提示 → installed=False, version=None（宁可漏报不误报）。"""
    from app.services import envcheck

    SHIM_TEXT = ("Python was not found; run without arguments to install from the "
                 "Microsoft Store, or disable this shortcut from "
                 "Settings > Manage App Execution Aliases.")

    class FakeRun:
        stdout = SHIM_TEXT
        stderr = ""

    monkeypatch.setattr(envcheck.shutil, "which", lambda exe: f"C:/fake/{exe}")
    monkeypatch.setattr(envcheck.subprocess, "run", lambda *a, **k: FakeRun())
    result = {r["name"]: r for r in envcheck.probe_versions()}
    assert result["python"]["installed"] is False
    assert result["python"]["version"] is None


def test_probe_versions_accepts_real_version(monkeypatch):
    """正常版本输出（Python 3.12.1 / v20.9.0）→ installed=True。"""
    from app.services import envcheck

    outs = {"python": "Python 3.12.1", "node": "v20.9.0", "bash": "GNU bash, version 5.2.0(1)",
            "powershell": "7.4.1"}

    class FakeRun:
        def __init__(self, cmd):
            self.stdout = outs[cmd[0]] if cmd[0] in outs else "1.2.3"
            self.stderr = ""

    monkeypatch.setattr(envcheck.shutil, "which", lambda exe: f"/usr/bin/{exe}")
    monkeypatch.setattr(envcheck.subprocess, "run", lambda cmd, **k: FakeRun(cmd))
    result = {r["name"]: r for r in envcheck.probe_versions()}
    for name in result:
        assert result[name]["installed"] is True, name


def test_version_cmds_platform_trimmed():
    """Windows 不探 python3（Store 垫片场景无意义）；键名统一叫 python。"""
    from app.services import envcheck
    assert "python3" not in envcheck._VERSION_CMDS
    assert "python" in envcheck._VERSION_CMDS


# ── N2：高危脚本自动标记 ─────────────────────────────────────────────────────
@pytest.fixture()
def scan_env(tmp_path, monkeypatch):
    """隔离 DATA_DIR/配置，再导入 app（config 在 import 时解析路径）。"""
    data = tmp_path / "data"
    cfgdir = tmp_path / "cfghome"
    monkeypatch.setenv("SCRIPTHUB_DATA_DIR", str(data))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfgdir))
    monkeypatch.delenv("SCRIPTHUB_SCRIPTS_ROOT", raising=False)
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]
    from app import config
    import app.models  # noqa: F401
    from app.database import init_db

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    asyncio.run(init_db())
    yield config, tmp_path
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]


def test_scanner_auto_flags_dangerous(scan_env):
    from app.database import async_session
    from app.services.scanner import scan_scripts
    from app.models.script import Script
    from sqlalchemy import select

    config, tmp_path = scan_env
    root = tmp_path / "scripts"
    root.mkdir()
    config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.CONFIG_PATH.write_text(f'{{"script_root_dir": "{root}"}}')

    (root / "bad.py").write_text("import os\nos.system('shutdown /r /t 60')\n")
    (root / "evil.sh").write_text("#!/bin/bash\nrm -rf /\n")
    (root / "good.py").write_text("print('hello')\n")

    async def _go():
        async with async_session() as db:
            await scan_scripts(db)
            rows = (await db.execute(select(Script))).scalars().all()
            return {s.name: s.dangerous for s in rows}

    flagged = asyncio.run(_go())
    assert flagged["bad.py"] is True
    assert flagged["evil.sh"] is True
    assert flagged["good.py"] is False


def test_scanner_respects_manual_false(scan_env):
    """手动标 False 的行不因规则翻 True；已 True 的保持 True。"""
    from app.database import async_session
    from app.services.scanner import scan_scripts
    from app.models.script import Script
    from sqlalchemy import select

    config, tmp_path = scan_env
    root = tmp_path / "scripts"
    root.mkdir()
    config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.CONFIG_PATH.write_text(f'{{"script_root_dir": "{root}"}}')

    (root / "d1.py").write_text("os.system('shutdown /r')\n")

    async def _scan():
        async with async_session() as db:
            await scan_scripts(db)

    asyncio.run(_scan())

    async def _flip():
        async with async_session() as db:
            s = (await db.execute(select(Script))).scalar_one()
            s.dangerous = False  # 用户手动撤销
            await db.commit()

    asyncio.run(_flip())
    asyncio.run(_scan())  # mtime 未变 → 不该重扫改写；即便重扫也不许翻回 True

    async def _read():
        async with async_session() as db:
            return (await db.execute(select(Script))).scalar_one().dangerous

    assert asyncio.run(_read()) is False


# ── N10：删设备清孤儿凭据 ────────────────────────────────────────────────────
def test_delete_service_removes_all_usernames(tmp_path, monkeypatch):
    """历史 username（yy@… 与 admin@…）全部被删，keyring 无 scripthub-device-* 残留。"""
    monkeypatch.setenv("SCRIPTHUB_SECRETS_FILE", str(tmp_path / "secrets.json"))
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]
    from app.services import secret_store

    class FakeCred:
        username = "admin"

    class FakeKeyring:
        """有后端时 get_credential 能兜到文件外的条目。"""
        def __init__(self):
            self.store = {("scripthub-device-1", "legacy"): "x"}

        def set_password(self, service, username, secret):
            self.store[(service, username)] = secret

        def get_password(self, service, username):
            return self.store.get((service, username))

        def delete_password(self, service, username):
            self.store.pop((service, username), None)

        def get_credential(self, service, username):
            names = {u for (s, u) in self.store if s == service}
            return FakeCred() if names else None

    kr = FakeKeyring()
    monkeypatch.setattr(secret_store, "_keyring", kr, raising=False)

    # 创建时用户名 legacy，后更新为 admin + 又更新为 yy → 3 个条目
    secret_store.set_secret("scripthub-device-1", "legacy", "p1")
    secret_store.set_secret("scripthub-device-1", "admin", "p2")
    secret_store.set_secret("scripthub-device-1", "yy", "p3")

    secret_store.delete_service("scripthub-device-1")

    assert kr.store == {}  # keyring 无任何残留
    assert secret_store._file_secrets().get("scripthub-device-1", {}) == {}


# ── N9：SCRIPTHUB_DEFAULT_DATA_DIR 迁移分支 ─────────────────────────────────
def _load_entry():
    import importlib.util
    spec = importlib.util.spec_from_file_location("bn_entry", BACKEND / "entry.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_entry_migrates_with_default_data_dir(tmp_path, monkeypatch):
    """只注入 DEFAULT_DATA_DIR + OLD_DATA_DIR（Tauri 壳的真实形态）→ main 执行迁移。

    main 随后会 uvicorn.run——monkeypatch 掉它，捕获传入的 port/data_dir 打印即可。
    """
    old_dir = tmp_path / "olddata"
    new_dir = tmp_path / "newdata"
    old_dir.mkdir()
    # 旧库内的 scripts.path 指旧 root → 迁移要做路径修复（证明走了 migrate_if_needed 全流程）
    import sqlite3
    conn = sqlite3.connect(old_dir / "scripthub.db")
    conn.execute("CREATE TABLE scripts (path TEXT)")
    conn.execute("INSERT INTO scripts VALUES (?)", (str(old_dir / "scripts" / "a.py"),))
    conn.execute("CREATE TABLE run_history (output_file TEXT)")
    conn.commit()
    conn.close()
    (old_dir / "config.json").write_text('{"script_root_dir": ""}')

    monkeypatch.delenv("SCRIPTHUB_DATA_DIR", raising=False)
    monkeypatch.setenv("SCRIPTHUB_DEFAULT_DATA_DIR", str(new_dir))
    monkeypatch.setenv("SCRIPTHUB_OLD_DATA_DIR", str(old_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))

    mod = _load_entry()

    class _UvicornCalled(Exception):
        pass

    class _Stub:
        @staticmethod
        def run(*a, **k):
            raise _UvicornCalled()

    # uvicorn 在 main() 内 import → stub sys.modules 项
    import types
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=_Stub.run))
    with pytest.raises(_UvicornCalled):
        mod.main()

    # 迁移产物：新目录有 db + marker + 修复后的路径
    assert (new_dir / "scripthub.db").exists()
    assert (new_dir / ".migrated_from").exists()
    conn = sqlite3.connect(new_dir / "scripthub.db")
    fixed = conn.execute("SELECT path FROM scripts").fetchone()[0]
    conn.close()
    assert fixed == str(new_dir / "scripts" / "a.py")


def test_entry_data_dir_priority(tmp_path, monkeypatch):
    """显式 SCRIPTHUB_DATA_DIR 仍优先于 DEFAULT（测试/开发覆盖语义不变）。"""
    monkeypatch.setenv("SCRIPTHUB_DATA_DIR", str(tmp_path / "explicit"))
    monkeypatch.setenv("SCRIPTHUB_DEFAULT_DATA_DIR", str(tmp_path / "default"))
    mod = _load_entry()
    data_dir = (mod.os.environ.get("SCRIPTHUB_DATA_DIR")
                or mod.os.environ.get("SCRIPTHUB_DEFAULT_DATA_DIR"))
    assert data_dir == str(tmp_path / "explicit")
