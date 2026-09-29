"""批次 AD ①（终态落库不依赖前端连接）+ ③（.sh 的平台判定口径）。

① 真机现象：脚本执行**已完成**，运行历史那条记录仍显示「运行中」，要切页/下次执行才变。
   两层原因，分别在这两个层面回归：
   - 落库侧：终态由 executor 在**执行结束时**同步写入（与任何 websocket 连接无关）。
     本文件用**真执行**（本机 bash 子进程）验证：不建立任何 websocket、不碰前端，
     记录也必须自己走到 success + finished_at + duration。
   - 进程侧：asyncio 只对 Task 持弱引用，`create_task(_run())` 不存引用时任务可能中途被 GC
     → 状态永远停在 running，且再没人写终态（spawn_background 修复）；进程重启留下的
     running 记录由启动收尾 fail_stale_running 落终态。

③ `.sh` 在 Windows 上不是无条件可跑（要 Git Bash/MSYS2/WSL 的 bash），也不是一律判死。

跑法：cd backend && .venv/bin/python -m pytest tests/test_run_terminal_state.py -q
"""
import asyncio
import importlib
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

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


# ── ① 终态落库：真执行 + 零 websocket ────────────────────────────────────────────────

def test_run_lands_terminal_state_without_any_frontend(isolated):
    """提交一次真执行（本机 bash 子进程），全程**不建立任何 websocket**：
    记录必须自己变成终态，且 finished_at / duration 都有值。"""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import async_session
    from app.models.script import Script

    sh = isolated["tmp"] / "hello.sh"
    sh.write_text("#!/usr/bin/env bash\nsleep 0.3\necho done\n")

    async def _seed():
        async with async_session() as db:
            db.add(Script(name="hello.sh", path=str(sh), relative_path="hello.sh",
                          extension=".sh", category="shell"))
            await db.commit()

    asyncio.run(_seed())

    # with 块：TestClient 的事件循环在整个测试期间存活（fire-and-forget 的执行任务需要它）
    with TestClient(app) as c:
        r = c.post("/api/run", json={"script_id": 1})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "running"      # 提交瞬间确实是 running

        row = {}
        for _ in range(100):                        # 只**读**，不做任何"补状态"的动作
            row = c.get("/api/run/history", params={"page_size": 5}).json()["items"][0]
            if row["status"] != "running":
                break
            time.sleep(0.1)

    assert row["status"] == "success", row
    assert row["exit_code"] == 0, row
    assert row["finished_at"], row
    assert row["duration"] and row["duration"] > 0, row
    assert "done" in (row["output"] or ""), row
    print(f"\n[AD①] 真执行落库 duration={row['duration']!r} finished_at={row['finished_at']!r}")


def test_executor_writes_terminal_state_direct(isolated):
    """不经 HTTP 直接驱动 executor（等价路径）：同一保证 —— 终态由 executor 写，不靠连接。"""
    from app.database import async_session
    from app.models.run_history import RunHistory
    from app.models.script import Script
    from app.services.executor import executor

    sh = isolated["tmp"] / "quick.sh"
    sh.write_text("#!/usr/bin/env bash\necho quick\n")

    async def _go():
        async with async_session() as db:
            db.add(Script(name="quick.sh", path=str(sh), relative_path="quick.sh",
                          extension=".sh", category="shell"))
            await db.commit()
            rh = RunHistory(script_id=1, command="", status="running", started_at=datetime.now())
            db.add(rh)
            await db.commit()
            await db.refresh(rh)
            rid = rh.id

        script = Script(name="quick.sh", path=str(sh), relative_path="quick.sh",
                        extension=".sh", category="shell")
        await executor.execute_script(script=script, run_history_id=rid, parameters={}, timeout=0)

        async with async_session() as db:
            got = (await db.execute(
                select(RunHistory).where(RunHistory.id == rid)
            )).scalar_one()
            return got.status, got.exit_code, got.duration, got.finished_at

    status, exit_code, duration, finished_at = asyncio.run(_go())
    assert (status, exit_code) == ("success", 0)
    assert finished_at is not None and duration and duration > 0


def test_run_exception_path_lands_terminal_with_duration(isolated, monkeypatch):
    """执行任务本身抛异常（executor 炸了）→ run.py 的兜底也必须落终态三件套，不能停在 running。"""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import async_session
    from app.models.script import Script
    from app.services.executor import ScriptExecutor

    async def _seed():
        async with async_session() as db:
            db.add(Script(name="boom.sh", path=str(isolated["tmp"] / "boom.sh"),
                          relative_path="boom.sh", extension=".sh", category="shell"))
            await db.commit()

    asyncio.run(_seed())

    async def _boom(self, **kwargs):        # 打类不打实例（实例属性会遮蔽类方法）
        raise RuntimeError("模拟执行失败")

    monkeypatch.setattr(ScriptExecutor, "execute_script", _boom)

    with TestClient(app) as c:
        assert c.post("/api/run", json={"script_id": 1}).status_code == 200
        row = {}
        for _ in range(100):
            row = c.get("/api/run/history", params={"page_size": 5}).json()["items"][0]
            if row["status"] != "running":
                break
            time.sleep(0.1)

    assert row["status"] == "failed", row
    assert row["exit_code"] == -1 and "执行异常" in row["output"], row
    assert row["finished_at"] and row["duration"] is not None, row


def test_spawn_background_keeps_strong_reference():
    """① asyncio 只对 Task 持弱引用：不存引用时后台任务可能在执行中途被 GC（状态永远停在 running）。
    spawn_background 必须在任务结束前持有引用，结束后释放。"""
    executor_mod = importlib.import_module("app.services.executor")

    async def _go():
        async def _work():
            await asyncio.sleep(0.01)
            return 7

        task = executor_mod.spawn_background(_work())
        assert task in executor_mod._BACKGROUND_TASKS, "后台任务没有被强引用（可能被 GC 掉）"
        assert await task == 7
        await asyncio.sleep(0)          # 让 done_callback 执行
        assert task not in executor_mod._BACKGROUND_TASKS, "任务结束后引用未释放（集合会一直长大）"

    asyncio.run(_go())


def test_fail_stale_running_落终态且幂等(isolated):
    """① 进程重启后遗留的 running 记录：没人再能推进它 → 启动收尾必须落终态。"""
    from app.database import async_session
    from app.models.run_history import RunHistory
    from app.services.executor import fail_stale_running

    async def _go():
        async with async_session() as db:
            db.add(RunHistory(script_id=None, command="stale", status="running",
                              output="", started_at=datetime.now() - timedelta(seconds=5)))
            db.add(RunHistory(script_id=None, command="done", status="success",
                              started_at=datetime.now(), finished_at=datetime.now()))
            await db.commit()

        n = await fail_stale_running()
        async with async_session() as db:
            rows = {r.command: r for r in (await db.execute(select(RunHistory))).scalars().all()}
        return n, rows

    n, rows = asyncio.run(_go())
    assert n == 1, "只该收尾 running 的那条"
    stale = rows["stale"]
    assert (stale.status, stale.exit_code) == ("failed", -1)
    assert stale.finished_at is not None and stale.duration and stale.duration >= 5
    assert "中断" in stale.output
    assert rows["done"].status == "success", "已终态的行不得被动到"
    assert asyncio.run(fail_stale_running()) == 0, "幂等：无 running 时应返回 0"


# ── ③ .sh 的平台判定：Windows 上要 bash 才算可跑 ─────────────────────────────────────

def _platform_check(checks):
    return next(c for c in checks if c["type"] == "platform")


def test_shell_on_unix_is_ok(isolated):
    env = importlib.import_module("app.services.envcheck")
    from app.models.script import Script

    s = Script(id=1, name="x.sh", path="/x/x.sh", relative_path="x.sh",
               extension=".sh", category="shell")
    plat = _platform_check(env.check_environment(s))
    assert plat["ok"] is True and plat["actual"] == "unix" and plat["detail"] == "可执行"


def test_shell_on_windows_depends_on_bash(isolated, monkeypatch):
    """Windows + .sh：装了 bash（Git Bash/MSYS2/WSL）→ 可跑；没装 → 不可跑且文案点名 Git Bash。"""
    env = importlib.import_module("app.services.envcheck")
    from app.models.script import Script

    s = Script(id=1, name="x.sh", path="C:/x/x.sh", relative_path="x.sh",
               extension=".sh", category="shell")
    monkeypatch.setattr(env, "current_platform", lambda: "windows")

    monkeypatch.setattr(env, "has_runtime", lambda exe: exe == "bash")
    plat = _platform_check(env.check_environment(s))
    assert plat["ok"] is True, plat
    assert "bash" in plat["detail"], plat
    assert "Git Bash" in plat["required"], plat

    monkeypatch.setattr(env, "has_runtime", lambda exe: False)
    plat = _platform_check(env.check_environment(s))
    assert plat["ok"] is False, plat
    assert "Git Bash" in plat["required"], plat
    assert "未检测到 bash" in plat["detail"], plat


def test_windows_only_categories_and_python_unchanged(isolated, monkeypatch):
    """.bat/.ps1 仍仅 Windows；python 仍跨平台；例外只给 shell，不给别的类别。"""
    env = importlib.import_module("app.services.envcheck")
    from app.models.script import Script

    def mk(cat, ext):
        return Script(id=1, name=f"x{ext}", path=f"/x/x{ext}", relative_path=f"x{ext}",
                      extension=ext, category=cat)

    monkeypatch.setattr(env, "has_runtime", lambda exe: True)   # 装了 bash 也不该让 .bat 在 unix 可跑
    assert _platform_check(env.check_environment(mk("bat", ".bat")))["ok"] is False
    assert _platform_check(env.check_environment(mk("powershell", ".ps1")))["ok"] is False
    assert _platform_check(env.check_environment(mk("python", ".py")))["ok"] is True

    monkeypatch.setattr(env, "current_platform", lambda: "windows")
    assert _platform_check(env.check_environment(mk("bat", ".bat")))["ok"] is True
    assert _platform_check(env.check_environment(mk("powershell", ".ps1")))["ok"] is True
    assert _platform_check(env.check_environment(mk("python", ".py")))["ok"] is True
