"""批次 BQ 三修回归：①本机 py/sh 输出解码 ②参数键归一化 ③（build_sidecar.sh 见 bash 自测）。

跑法：cd backend && .venv/bin/python -m pytest tests/test_bq_local_codec_and_params.py -q
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
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
    yield {"config": config, "tmp": tmp_path}
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]


# ── ① 本机输出解码按类别 ──────────────────────────────────────────────────────────────

def test_codec_by_category_win(monkeypatch):
    """Windows 本机：python/bash 类解 UTF-8（自己发 UTF-8），cmd/PS 维持 ANSI 旋钮。"""
    from app.services import executor

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv(executor.WIN_ANSI_CODEC_ENV, "cp936")
    assert executor._local_output_codec("python") == "utf-8"
    assert executor._local_output_codec("shell") == "utf-8"
    assert executor._local_output_codec("bat") == "cp936"
    assert executor._local_output_codec("powershell") == "cp936"
    assert executor._local_output_codec(None) == "cp936"        # 不带类别 = 旧行为
    monkeypatch.setattr(sys, "platform", "linux")
    assert executor._local_output_codec("bat") == "utf-8"       # Unix 一律 UTF-8 不变


def test_python_local_run_injects_utf8_env_and_decodes_utf8(isolated, monkeypatch):
    """真执行回归：Windows 本机 .py 输出 UTF-8「中文」，此前按 GBK 解成 `涓枂`（R1/R18 根因）。"""
    from datetime import datetime

    from app.database import async_session
    from app.models.run_history import RunHistory
    from app.models.script import Script
    from app.services.executor import ScriptExecutor

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SCRIPTHUB_WIN_ANSI_CODEC", "cp936")

    captured_env = {}
    real_py = sys.executable

    class FakeProc:
        pid = 0
        stdout: object = None
        stderr: object = None
        returncode = 0

        async def wait(self):
            return 0

    async def fake_shell(*args, **kwargs):
        captured_env.update(kwargs.get("env") or {})
        # 模拟「python 发 UTF-8」：脚本打中文，stdout 编码按 PYTHONIOENCODING
        p = FakeProc()
        enc = (captured_env.get("PYTHONIOENCODING") or "gbk").split(":")[0]
        import io

        class _S:
            def __init__(self, data):
                self._buf = io.BytesIO(data)

            async def readline(self):
                line = self._buf.readline()
                return line if line else b""

        p.stdout = _S("中文OK\n".encode(enc))
        p.stderr = _S(b"")
        return p

    ex = ScriptExecutor()
    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)
    monkeypatch.setattr("app.services.executor.resolve_python", lambda: "python")
    monkeypatch.setattr(ex, "_kill_process_tree", lambda p: asyncio.sleep(0))

    sh = isolated["tmp"] / "hello.py"
    sh.write_text("print('中文OK')\n", encoding="utf-8")
    script = Script(name="hello.py", path=str(sh), relative_path="hello.py",
                    extension=".py", category="python", path_override=None) \
        if False else Script(name="hello.py", path=str(sh), relative_path="hello.py",
                             extension=".py", category="python")

    async def _go():
        async with async_session() as db:
            db.add(Script(name="hello.py", path=str(sh), relative_path="hello.py",
                          extension=".py", category="python"))
            await db.commit()
            rh = RunHistory(script_id=1, command="", status="running", started_at=datetime.now())
            db.add(rh)
            await db.commit()
            await db.refresh(rh)
            return rh.id

    rid = asyncio.run(_go())
    # Windows 上 resolve_python 可能不可用，但 fake 不真跑——只看 env 与解码
    asyncio.run(ex.execute_script(script=script, run_history_id=rid, parameters={}))
    assert captured_env.get("PYTHONIOENCODING") == "utf-8", captured_env

    from sqlalchemy import select
    from app.database import async_session as sess

    async def _out():
        async with sess() as db:
            return (await db.execute(select(RunHistory).where(RunHistory.id == rid))).scalar_one()

    row = asyncio.run(_out())
    assert "中文OK" in row.output, row.output
    assert "\ufffd" not in row.output, row.output


# ── ② 参数键归一化 ────────────────────────────────────────────────────────────────────

def _meta_script(**kw):
    from app.models.script import Script
    return Script(id=1, name="j.py", path="/x/j.py", relative_path="j.py", extension=".py",
                  category="python",
                  parameters=json.dumps([{"name": "--count", "type": "int"}]), **kw)


def test_normalize_bare_key_maps_to_meta():
    from app.services.executor import normalize_parameters
    s = _meta_script()
    assert normalize_parameters(s, {"count": "5"}) == {"--count": "5"}
    assert normalize_parameters(s, {"--count": "5"}) == {"--count": "5"}   # 已带前缀幂等
    assert normalize_parameters(s, {"-count": "5"}) == {"--count": "5"}
    assert normalize_parameters(s, {}) == {}


def test_normalize_unknown_key_raises():
    from app.services.executor import normalize_parameters
    with pytest.raises(ValueError, match="未知参数"):
        normalize_parameters(_meta_script(), {"nope": "1"})


def test_validate_parameters_bare_key_typed():
    """裸键现在能查到类型：count=abc 必须 400 拦（此前查不到类型被放行 → argparse 才炸）。"""
    from app.services.executor import validate_parameters
    s = _meta_script()
    validate_parameters(s, {"count": "5"})
    with pytest.raises(ValueError, match="count"):
        validate_parameters(s, {"count": "abc"})


def test_bare_key_run_request_400_and_command_has_dashdash(isolated, monkeypatch):
    """端到端：裸键非法值 → 400 带参数名；合法值 → 200 且命令行出现 --count；未知键 → 400。"""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import async_session
    from app.models.script import Script

    p = isolated["tmp"] / "job.py"
    p.write_text("print(1)\n")
    params = json.dumps([{"name": "--count", "type": "int"}])

    async def _go():
        async with async_session() as db:
            db.add(Script(name="job.py", path=str(p), relative_path="job.py",
                          extension=".py", category="python", parameters=params))
            await db.commit()

    asyncio.run(_go())

    from app.services.executor import ScriptExecutor
    built = {}
    real_build = ScriptExecutor._build_command

    def spy(self, script, parameters, **kw):
        built["cmd"] = real_build(self, script, parameters, **kw)
        return built["cmd"]

    monkeypatch.setattr(ScriptExecutor, "_build_command", spy)
    from app.services.executor import spawn_background
    monkeypatch.setattr(ScriptExecutor, "execute_script",
                        lambda self, **kw: asyncio.sleep(0))

    with TestClient(app) as c:
        bad = c.post("/api/run", json={"script_id": 1, "parameters": {"count": "abc"},
                                       "confirm_env": True})
        unknown = c.post("/api/run", json={"script_id": 1, "parameters": {"zzz": "1"},
                                           "confirm_env": True})
        good = c.post("/api/run", json={"script_id": 1, "parameters": {"count": "5"},
                                        "confirm_env": True})
    assert bad.status_code == 400 and "count" in bad.json()["detail"], bad.text
    assert unknown.status_code == 400 and "zzz" in unknown.json()["detail"], unknown.text
    assert good.status_code == 200, good.text
    assert "--count" in built["cmd"], built["cmd"]
