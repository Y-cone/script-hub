"""扫描器批量删除保护闸 + 运行历史保留回归（根因：scripts_root 切换 → 旧 root 脚本被批量删除 → 历史级联清空）。

跑法：cd backend && .venv/bin/python -m pytest tests/test_scanner_history.py -q
"""
import asyncio
import sys
from datetime import datetime
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


def _seed(config, root: Path, n: int):
    """在 root 下落 n 个脚本文件并扫描入库，每个脚本配一条运行历史。返回历史行数。"""
    from app.database import async_session
    from app.services.scanner import scan_scripts
    from app.models.script import Script
    from app.models.run_history import RunHistory
    from sqlalchemy import select

    _set_root(config, root)  # 先把扫描 root 指向 seed 目录（默认 root 是 DATA_DIR/scripts，不是这里）
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (root / f"s{i}.py").write_text(f"print({i})\n")

    async def _go():
        async with async_session() as db:
            await scan_scripts(db)
            scripts = (await db.execute(select(Script))).scalars().all()
            for s in scripts:
                db.add(RunHistory(
                    script_id=s.id, command="x", status="success",
                    started_at=datetime.now(), finished_at=datetime.now(),
                ))
            await db.commit()

    asyncio.run(_go())
    return n


def _set_root(config, root: Path):
    """改 XDG_CONFIG_HOME 下（已被 fixture monkeypatch 隔离）的 settings.json。"""
    config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.CONFIG_PATH.write_text(f'{{"script_root_dir": "{root}"}}')


def test_root_switch_preserves_history(isolated):
    """换 root 重扫：旧脚本被删，但运行历史必须保留（script_id 置 NULL）。"""
    from app import config
    from app.database import async_session
    from app.services.scanner import scan_scripts
    from app.models.script import Script
    from app.models.run_history import RunHistory
    from sqlalchemy import func, select

    tmp = isolated["tmp"]
    old_root, new_root = tmp / "old_root", tmp / "new_root"
    _seed(config, old_root, 2)

    # 切到新 root（旧 root 的 2 个脚本全部"消失"）
    new_root.mkdir(parents=True, exist_ok=True)
    (new_root / "other.py").write_text("print('new')\n")
    _set_root(config, new_root)

    async def _go():
        nonlocal total_scripts, hist_count, script_ids
        async with async_session() as db:
            await scan_scripts(db)
            total_scripts = (await db.execute(select(func.count(Script.id)))).scalar()
            hist_count = (await db.execute(select(func.count(RunHistory.id)))).scalar()
            script_ids = (await db.execute(select(RunHistory.script_id))).scalars().all()
    total_scripts = hist_count = None
    script_ids = []
    asyncio.run(_go())

    assert total_scripts == 1          # 旧 2 个被删，新 root 1 个入库
    assert hist_count == 2             # 运行历史一条不丢
    assert all(sid is None for sid in script_ids)  # script_id 悬空（SET NULL）


def test_batch_delete_guard(isolated):
    """批量删除保护闸：当前 root 内待删数 > max(5, 现存*0.5) 时跳过全部删除，脚本一个都不动。"""
    from app.database import async_session
    from app.services.scanner import scan_scripts
    from app.models.script import Script
    from sqlalchemy import func, select

    tmp = isolated["tmp"]
    root = tmp / "guard_root"
    _seed(config := isolated["config"], root, 10)

    # 同 root 下磁盘删掉 6/10 个文件（60% 消失）：保护闸必须拦住，不许静默清库
    for i in range(6):
        (root / f"s{i}.py").unlink()

    async def _go():
        nonlocal result, total
        async with async_session() as db:
            result = await scan_scripts(db)
            total = (await db.execute(select(func.count(Script.id)))).scalar()
    result = total = None
    asyncio.run(_go())

    assert result["removed"] == 0      # 保护闸生效
    assert total == 10                 # 脚本全在


def test_root_switch_deletes_stale_root_entries(isolated):
    """N8：根切到 B 后，旧 root A 的条目直接删除（不走保护闸），B 的条目保留。"""
    from app import config
    from app.database import async_session
    from app.services.scanner import scan_scripts
    from app.models.script import Script
    from sqlalchemy import select

    tmp = isolated["tmp"]
    root_a, root_b = tmp / "root_a", tmp / "root_b"
    _seed(config, root_a, 2)

    root_b.mkdir(parents=True, exist_ok=True)
    (root_b / "b1.py").write_text("print('b1')\n")
    _set_root(config, root_b)

    async def _go():
        nonlocal result, names
        async with async_session() as db:
            result = await scan_scripts(db)
            names = [s.name for s in (await db.execute(select(Script))).scalars()]
    result = names = None
    asyncio.run(_go())

    assert result["removed"] == 2      # 旧 root A 的 2 个条目被删
    assert sorted(names) == ["b1.py"]  # 只剩新 root B 的条目
