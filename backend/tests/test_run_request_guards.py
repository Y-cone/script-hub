"""批次 BM ⑦/N1、N6、④/N4、③/N3 + ②/K1：本机执行链路的入口守门与落盘转码。

覆盖（每条后面是它挡住的真机现象）：
  · N6  shell 覆盖必须与脚本类型同族：`{hello.bat, shell:"bash"}` 此前 200 并执行 `bash hello.bat`；
        `.sh + bash` / `.py + python3` 这类同族覆盖仍必须 200。
  · N1  `parameters={"--count": "abc"}` 此前被 200 接受（脚本跑到 argparse 才炸）→ 现在 400 且带参数名；
        请求体带未知字段（如误写 `params`）→ 422，不再静默忽略。
  · N4  路径含空格/中文要加引号（`cmd /c C:\\脚本 目录\\中文 测试.bat` 会在空格处截断）。
  · N3  DB 输出保留头部 200 行 + 尾部 800 行（此前只留尾部 1000 行，首行永远看不到）。
  · K1  Windows 本机上传/保存 .bat 落盘即 ANSI+CRLF（此前只有 SFTP 上传路径转码）。

跑法：cd backend && .venv/bin/python -m pytest tests/test_run_request_guards.py -q
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
    """隔离 DATA_DIR / OS 配置目录 / env，再导入 app（config.py 在 import 时解析路径）。"""
    data = tmp_path / "data"
    cfgdir = tmp_path / "cfghome"
    monkeypatch.setenv("SCRIPTHUB_DATA_DIR", str(data))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfgdir))
    monkeypatch.delenv("SCRIPTHUB_SCRIPTS_ROOT", raising=False)
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]
    from app import config
    import app.models  # noqa: F401 — 注册全部 ORM 模型，create_all 才有表可建
    from app.database import init_db

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    asyncio.run(init_db())
    yield {"config": config, "tmp": tmp_path}
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]


def _seed(tmp_path: Path, name: str, category: str, content: str, parameters: str = "[]") -> Path:
    """造一个磁盘脚本 + DB 记录（隔离库里的第一条 = script_id 1），返回脚本路径。"""
    from app.database import async_session
    from app.models.script import Script

    p = tmp_path / name
    p.write_text(content, encoding="utf-8")

    async def _go():
        async with async_session() as db:
            db.add(Script(name=name, path=str(p), relative_path=name, extension=p.suffix,
                          category=category, parameters=parameters))
            await db.commit()

    asyncio.run(_go())
    return p


@pytest.fixture()
def no_exec(monkeypatch, isolated):
    """拦掉真实执行：本文件只验证"请求该不该被受理"，不真跑脚本。"""
    from app.services.executor import ScriptExecutor

    calls = []

    async def _noop(self, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(ScriptExecutor, "execute_script", _noop)
    monkeypatch.setattr(ScriptExecutor, "execute_remote", _noop)
    return calls


# ── N6：shell 覆盖 × 脚本类型 ─────────────────────────────────────────────────────────

def test_bat_with_bash_shell_is_rejected_400(isolated, monkeypatch):
    """真机: `POST /api/run {script_id: <bat>, shell:"bash"}` 返回 200 并执行了 `bash hello.bat`。"""
    from fastapi.testclient import TestClient
    from app.main import app

    _seed(isolated["tmp"], "hello.bat", "bat", "@echo off\r\necho hi\r\n")
    with TestClient(app) as c:
        r = c.post("/api/run", json={"script_id": 1, "shell": "bash",
                                     "confirm_env": True, "confirm_dangerous": True})
    assert r.status_code == 400, r.text
    assert "bash" in r.json()["detail"] and "bat" in r.json()["detail"], r.text


def test_python_with_bash_shell_is_rejected_400(isolated):
    """同族之外一律拒绝：.py 用 bash 跑同样是错的组合。"""
    from fastapi.testclient import TestClient
    from app.main import app

    _seed(isolated["tmp"], "x.py", "python", "print('hi')\n")
    with TestClient(app) as c:
        r = c.post("/api/run", json={"script_id": 1, "shell": "bash", "confirm_env": True})
    assert r.status_code == 400, r.text


@pytest.mark.parametrize("name,category,content,shell", [
    ("ok.sh", "shell", "#!/usr/bin/env bash\necho ok\n", "bash"),
    ("ok.py", "python", "print('ok')\n", "python3"),
])
def test_same_family_shell_override_still_accepted(isolated, no_exec, name, category, content, shell):
    """合法组合必须照旧 200（不许把守卫做成"全拒绝"）。"""
    from fastapi.testclient import TestClient
    from app.main import app

    _seed(isolated["tmp"], name, category, content)
    with TestClient(app) as c:
        r = c.post("/api/run", json={"script_id": 1, "shell": shell, "confirm_env": True})
    assert r.status_code == 200, r.text
    assert no_exec, "执行未被触发（后台任务没起来）"


# ── N1：入参类型 + 未知字段 ──────────────────────────────────────────────────────────

def test_non_numeric_int_param_is_rejected_400(isolated):
    """真机: parameters={"count":"abc"} 被 200 接受；现在 400 且消息里带参数名。"""
    from fastapi.testclient import TestClient
    from app.main import app

    _seed(isolated["tmp"], "job.py", "python", "import argparse\n",
          parameters=json.dumps([{"name": "--count", "type": "int"}]))
    with TestClient(app) as c:
        bad = c.post("/api/run", json={"script_id": 1, "parameters": {"--count": "abc"},
                                       "confirm_env": True})
        good = c.post("/api/run", json={"script_id": 1, "parameters": {"--count": "5"},
                                        "confirm_env": True})
    assert bad.status_code == 400, bad.text
    assert "--count" in bad.json()["detail"], bad.text
    assert good.status_code == 200, good.text


def test_unknown_request_field_is_422(isolated):
    """误用 `params`（而非 parameters）此前被静默忽略 → 脚本收到空参数表；必须 422。"""
    from fastapi.testclient import TestClient
    from app.main import app

    _seed(isolated["tmp"], "job.py", "python", "print(1)\n")
    with TestClient(app) as c:
        r = c.post("/api/run", json={"script_id": 1, "params": {"a": 1}})
    assert r.status_code == 422, r.text


def test_validate_parameters_unit():
    """纯函数级：未知参数名不拦（脚本可能吃元数据没解析出的参数），非标量值与类型不符要拦。"""
    from app.models.script import Script
    from app.services.executor import validate_parameters

    s = Script(id=1, name="j.py", path="/x/j.py", relative_path="j.py", extension=".py",
               category="python", parameters=json.dumps([
                   {"name": "--n", "type": "int"}, {"name": "--r", "type": "float"},
                   {"name": "--f", "type": "bool"}, {"name": "--s", "type": "string"}]))
    validate_parameters(s, {})                                     # 空 → 放行
    validate_parameters(s, {"--n": 3, "--r": "1.5", "--s": "x y", "--f": True, "--other": "1"})
    for bad in ({"--n": "abc"}, {"--n": "1.5"}, {"--r": "x"}, {"--s": {"a": 1}}, {"--n": [1]}):
        try:
            validate_parameters(s, bad)
        except ValueError:
            continue
        raise AssertionError(f"未拒绝 {bad!r}")


# ── N4：路径/参数引号 ────────────────────────────────────────────────────────────────

def test_build_command_quotes_paths_and_args():
    from app.models.script import Script
    from app.services.executor import ScriptExecutor

    ex = ScriptExecutor()

    def cmd(category, path, params=None, shell=None):
        s = Script(id=1, name="x", path=path, relative_path="x", extension="",
                   category=category)
        return ex._build_command(s, params or {}, shell=shell)

    assert cmd("bat", r"C:\脚本 目录\中文 测试.bat") == r'cmd /c "C:\脚本 目录\中文 测试.bat"'
    assert cmd("powershell", r"C:\脚本 目录\a.ps1") == \
        r'powershell -ExecutionPolicy Bypass -File "C:\脚本 目录\a.ps1"'
    assert cmd("shell", "/srv/脚本 目录/a.sh") == 'bash "/srv/脚本 目录/a.sh"'
    assert cmd("bat", "/a/b.bat", {"arg": "a b"}) == 'cmd /c "/a/b.bat" --arg "a b"'
    # 参数值含空格/中文 → 加引号；纯 ASCII 单词同样加（统一写法，见 _quote 文档）
    # BQ②：参数名统一 -- 前缀（幂等），裸键/-n 不再原样透传
    assert cmd("shell", "/a.sh", {"-n": "张三 李四", "-f": True, "-z": ""}) == \
        'bash "/a.sh" --n "张三 李四" --f'


# ── N3：DB 输出头部 + 尾部 ───────────────────────────────────────────────────────────

def test_db_output_keeps_head_and_tail():
    from app.services.executor import DB_HEAD_LINES, DB_TAIL_LINES, _db_output

    assert _db_output("a\nb\nc") == "a\nb\nc"          # 短输出：一个字都不该动

    body = "\n".join(f"line{i}" for i in range(2000))
    out = _db_output(body)
    lines = out.split("\n")
    assert lines[0] == "line0", "首行丢失（真机 N3：2000 行输出的首部被整段裁掉）"
    assert lines[DB_HEAD_LINES - 1] == f"line{DB_HEAD_LINES - 1}"
    assert "省略" in lines[DB_HEAD_LINES]
    assert lines[-1] == "line1999", "尾行丢失"
    assert lines[-DB_TAIL_LINES] == f"line{2000 - DB_TAIL_LINES}"
    assert len(lines) == DB_HEAD_LINES + 1 + DB_TAIL_LINES


# ── ③/N5：本机输出解码（Windows = ANSI，Unix = UTF-8） ──────────────────────────────

def test_local_output_codec_only_windows(monkeypatch):
    from app.services import executor

    monkeypatch.setattr(sys, "platform", "linux")
    assert executor._local_output_codec() == "utf-8"        # Linux 回归：行为与改动前一致
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv(executor.WIN_ANSI_CODEC_ENV, "cp950")
    assert executor._local_output_codec() == "cp950"        # 旋钮可覆盖（繁中目标机）
    assert executor._local_output_codec("bat") == "cp950"   # cmd/PS 类仍走 ANSI 旋钮（BQ①）
    monkeypatch.setenv(executor.WIN_ANSI_CODEC_ENV, "no_such_codec")
    assert executor._local_output_codec() == "cp936"        # 非法值回落，不抛


def test_windows_local_output_is_decoded_as_ansi(isolated, monkeypatch):
    """③⑤ 真执行回归（BQ① 改写）：bat 类子进程发 GBK 字节，必须按 ANSI 解出中文。

    BQ① 之前用真 bash 发 GBK——但 bash/Git Bash 实际只发 UTF-8（真机无此场景），
    现在 cmd/bat 类才走 ANSI 解码；Linux 上没有 cmd，用假子进程发 cp936 字节验证解码端。
    """
    import io
    from datetime import datetime

    from app.database import async_session
    from app.models.run_history import RunHistory
    from app.models.script import Script
    from app.services.executor import ScriptExecutor

    class FakeProc:
        pid = 0
        stdout: object = None
        stderr: object = None
        returncode = 0

        async def wait(self):
            return 0

    async def fake_shell(*args, **kwargs):
        p = FakeProc()
        buf = io.BytesIO("中文\n".encode("cp936"))

        class _S:
            async def readline(self):
                return buf.readline()

        p.stdout = _S()
        p.stderr = _S()
        return p

    async def _go():
        async with async_session() as db:
            db.add(Script(name="gbk.bat", path="/x/gbk.bat", relative_path="gbk.bat",
                          extension=".bat", category="bat"))
            await db.commit()
            rh = RunHistory(script_id=1, command="", status="running", started_at=datetime.now())
            db.add(rh)
            await db.commit()
            await db.refresh(rh)
            rid = rh.id

        ex = ScriptExecutor()
        script = Script(name="gbk.bat", path="/x/gbk.bat", relative_path="gbk.bat",
                        extension=".bat", category="bat")
        await ex.execute_script(script=script, run_history_id=rid, parameters={})
        async with async_session() as db:
            from sqlalchemy import select
            return (await db.execute(select(RunHistory).where(RunHistory.id == rid))).scalar_one()

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SCRIPTHUB_WIN_ANSI_CODEC", "cp936")
    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)
    row = asyncio.run(_go())
    assert row.status == "success", row.output
    assert "中文" in row.output, row.output
    assert "\ufffd" not in row.output, row.output


# ── K1：Windows 本机落盘转码 ────────────────────────────────────────────────────────

def test_win_local_encode_only_on_windows_and_script_suffixes(monkeypatch):
    from app.routers.script import win_local_encode

    zh = "@echo off\n@REM 案例\nmkdir \"中文目录\"\n".encode()
    monkeypatch.setattr(sys, "platform", "win32")
    payload, warn = win_local_encode(zh, "cleanup.bat")
    assert warn is None and payload == zh.replace(b"\n", b"\r\n").decode().encode("cp936")
    assert win_local_encode(zh, "a.py") == (zh, None)        # .py 不转（python 自读）
    monkeypatch.setattr(sys, "platform", "linux")
    assert win_local_encode(zh, "cleanup.bat") == (zh, None)  # Unix 行为不变


def test_upload_bat_on_windows_lands_ansi_crlf(isolated, monkeypatch):
    """② K1：Windows 本机上传 .bat，落盘必须是 ANSI(cp936)+CRLF（此前原样 UTF-8 → cmd 按 GBK 读 → 乱码）。"""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.config import get_script_root

    zh = "@echo off\n@REM 案例 cleanup\nmkdir \"中文目录\" 2>nul\necho done\n"
    monkeypatch.setattr(sys, "platform", "win32")
    with TestClient(app) as c:
        r = c.post("/api/scripts/upload",
                   files={"file": ("cleanup.bat", zh.encode("utf-8"), "application/octet-stream")})
    assert r.status_code == 200, r.text
    raw = (get_script_root() / "cleanup.bat").read_bytes()
    assert raw == zh.replace("\n", "\r\n").encode("cp936"), raw[:80]


def test_save_bat_content_on_windows_lands_ansi_crlf(isolated, monkeypatch):
    """② K1：编辑器保存同样要转码（此前只有 SFTP 上传路径转，本机保存落的是 UTF-8）。"""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.config import get_script_root

    zh = "@echo off\n@REM 案例\nmkdir \"中文目录\"\n"
    root = get_script_root()
    root.mkdir(parents=True, exist_ok=True)   # 必须落在脚本根内：保存会触发重扫，根外文件会被移出库
    script_path = _seed(root, "save.bat", "bat", zh)
    monkeypatch.setattr(sys, "platform", "win32")
    with TestClient(app) as c:
        r = c.put("/api/scripts/1/content", json={"content": zh})
    assert r.status_code == 200, r.text
    assert r.json()["encoding"] == "cp936", r.text
    raw = script_path.read_bytes()
    assert raw == zh.replace("\n", "\r\n").encode("cp936"), raw[:80]
    # 再存一次（磁盘已是 ANSI）：不得因为"喂进去的不是 UTF-8"而误报警告
    with TestClient(app) as c:
        again = c.put("/api/scripts/1/content", json={"content": zh})
    assert again.status_code == 200 and again.json().get("encoding_warning") is None, again.text
