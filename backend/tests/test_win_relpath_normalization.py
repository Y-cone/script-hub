"""Windows 反斜杠泄漏修复：存量 relative_path 归一（幂等）+ GET /dirs 无反斜杠 +
归一后 rename 前缀同步正常（连锁数据风险回归）。

跑法：cd backend && .venv/bin/python -m pytest tests/test_win_relpath_normalization.py -q
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """隔离 DATA_DIR 后导入 app（同 test_dirs_rename_delete.py）。"""
    monkeypatch.setenv("SCRIPTHUB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfghome"))
    monkeypatch.delenv("SCRIPTHUB_SCRIPTS_ROOT", raising=False)
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]
    import app.models  # noqa: F401
    from app.database import init_db
    import asyncio
    asyncio.run(init_db())
    from app.main import app  # noqa: F401
    from app import config
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / "scripts").mkdir(exist_ok=True)
    yield {"app": app, "config": config, "tmp": tmp_path}
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]


def _seed_backslash_rows(isolated):
    """模拟 Windows 老库：直接塞反斜杠 relative_path 行（绕过 as_posix 写入侧）。"""
    import asyncio
    from app.database import async_session
    from app.models.script import Script
    from datetime import datetime

    async def _seed():
        async with async_session() as db:
            db.add(Script(name="a.py", path="/x/utils/test/a.py",
                          relative_path="utils\\test\\a.py",
                          extension=".py", category="python",
                          created_at=datetime.utcnow(), updated_at=datetime.utcnow()))
            db.add(Script(name="b.py", path="/x/b.py", relative_path="b.py",
                          extension=".py", category="python",
                          created_at=datetime.utcnow(), updated_at=datetime.utcnow()))
            await db.commit()

    asyncio.run(_seed())


def test_migration_normalizes_backslashes_and_is_idempotent(isolated):
    from app.database import _normalize_rel_path_separators
    import asyncio

    _seed_backslash_rows(isolated)

    asyncio.run(_normalize_rel_path_separators())

    import sqlite3
    db_file = isolated["config"].DATA_DIR / "scripthub.db"
    rows = dict(sqlite3.connect(db_file).execute("SELECT id, relative_path FROM scripts"))
    assert set(rows.values()) == {"utils/test/a.py", "b.py"}

    # 幂等：再跑一遍不产生任何变化
    asyncio.run(_normalize_rel_path_separators())
    rows2 = dict(sqlite3.connect(db_file).execute("SELECT id, relative_path FROM scripts"))
    assert rows2 == rows


def test_get_dirs_returns_no_backslash(isolated):
    c = TestClient(isolated["app"])
    root = isolated["config"].DATA_DIR / "scripts"
    (root / "utils" / "test").mkdir(parents=True, exist_ok=True)

    dirs = c.get("/api/scripts/dirs").json()["directories"]
    assert "utils/test" in dirs
    assert all("\\" not in d for d in dirs)


def test_rename_prefix_sync_after_normalization(isolated):
    """归一后的库上 rename 必须同步反斜杠来源的脚本行（PUT /dirs 前缀 LIKE 匹配）。"""
    c = TestClient(isolated["app"])
    root = isolated["config"].DATA_DIR / "scripts"

    _seed_backslash_rows(isolated)
    from app.database import _normalize_rel_path_separators
    import asyncio
    asyncio.run(_normalize_rel_path_separators())

    (root / "utils" / "test").mkdir(parents=True, exist_ok=True)
    r = c.put("/api/scripts/dirs", json={"old": "utils", "new": "tools"})
    assert r.status_code == 200, r.text

    items = c.get("/api/scripts", params={"page_size": 100}).json()["items"]
    rels = {s["name"]: s["relative_path"] for s in items}
    assert rels["a.py"] == "tools/test/a.py"
    assert rels["b.py"] == "b.py"
