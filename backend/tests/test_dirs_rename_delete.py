"""PUT/DELETE /api/scripts/dirs：目录重命名（磁盘 mv + DB 前缀同步）与仅空目录删除。

跑法：cd backend && .venv/bin/python -m pytest tests/test_dirs_rename_delete.py -q
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """隔离 DATA_DIR 后导入 app（同 test_dirs_create.py）。"""
    monkeypatch.setenv("SCRIPTHUB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfghome"))
    monkeypatch.delenv("SCRIPTHUB_SCRIPTS_ROOT", raising=False)
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]
    import app.models  # noqa: F401 — 注册全部 ORM 模型（须在 from app.main import app 之前，免得 shadow）
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


def _mk_script(root: Path, rel: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("print('x')\n", encoding="utf-8")


def test_rename_moves_disk_and_syncs_db(isolated):
    c = TestClient(isolated["app"])
    root = isolated["config"].DATA_DIR / "scripts"

    c.post("/api/scripts/dirs", json={"path": "a/b"})
    _mk_script(root, "a/b/old.py")
    c.post("/api/scripts/scan")

    r = c.put("/api/scripts/dirs", json={"old": "a/b", "new": "a/c"})
    assert r.status_code == 200, r.text

    # 磁盘：目录整体搬走，脚本文件跟着
    assert (root / "a" / "c" / "old.py").is_file()
    assert not (root / "a" / "b").exists()

    # DB：relative_path 前缀即时改写（不等 30s scanner）
    items = c.get("/api/scripts", params={"page_size": 100}).json()["items"]
    assert [s["relative_path"] for s in items] == ["a/c/old.py"]
    assert items[0]["path"].endswith("a/c/old.py")

    # GET /dirs 反映新结构
    dirs = c.get("/api/scripts/dirs").json()["directories"]
    assert "a/c" in dirs and "a/b" not in dirs


def test_rename_nests_multi_level_script(isolated):
    """多级目录重命名：old 下的嵌套子目录里脚本也同步"""
    c = TestClient(isolated["app"])
    root = isolated["config"].DATA_DIR / "scripts"

    _mk_script(root, "a/b/sub/deep.py")
    c.post("/api/scripts/scan")

    r = c.put("/api/scripts/dirs", json={"old": "a/b", "new": "x/y"})
    assert r.status_code == 200, r.text
    assert (root / "x" / "y" / "sub" / "deep.py").is_file()
    items = c.get("/api/scripts", params={"page_size": 100}).json()["items"]
    assert [s["relative_path"] for s in items] == ["x/y/sub/deep.py"]


def test_rename_conflict_traversal_missing(isolated):
    c = TestClient(isolated["app"])
    root = isolated["config"].DATA_DIR / "scripts"

    c.post("/api/scripts/dirs", json={"path": "a"})
    c.post("/api/scripts/dirs", json={"path": "b"})

    # new 已存在 → 400
    r = c.put("/api/scripts/dirs", json={"old": "a", "new": "b"})
    assert r.status_code == 400
    assert "已存在" in r.json()["detail"]

    # 穿越/绝对路径 → 400（old、new 两侧都拦）
    for bad in ({"old": "a", "new": "../x"}, {"old": "../a", "new": "x"},
                {"old": "a", "new": "/etc/x"}, {"old": "a", "new": "a/b/c"}):
        r = c.put("/api/scripts/dirs", json=bad)
        assert r.status_code == 400, (bad, r.status_code, r.text)

    # old 不存在 → 404
    r = c.put("/api/scripts/dirs", json={"old": "nope", "new": "x"})
    assert r.status_code == 404

    # 全部失败路径都没动磁盘
    assert (root / "a").is_dir() and (root / "b").is_dir()


def test_delete_empty_dir_ok_and_missing_404(isolated):
    c = TestClient(isolated["app"])
    root = isolated["config"].DATA_DIR / "scripts"

    c.post("/api/scripts/dirs", json={"path": "empty/nested"})
    r = c.delete("/api/scripts/dirs", params={"path": "empty/nested"})
    assert r.status_code == 200, r.text
    assert not (root / "empty" / "nested").exists()

    # 再删 → 404
    r = c.delete("/api/scripts/dirs", params={"path": "empty/nested"})
    assert r.status_code == 404


def test_delete_nonempty_rejected_with_count(isolated):
    c = TestClient(isolated["app"])
    root = isolated["config"].DATA_DIR / "scripts"

    _mk_script(root, "full/x.py")
    _mk_script(root, "full/sub/y.py")
    c.post("/api/scripts/scan")

    r = c.delete("/api/scripts/dirs", params={"path": "full"})
    assert r.status_code == 400
    assert "2 个脚本" in r.json()["detail"]
    assert (root / "full" / "x.py").is_file()   # 磁盘未动

    # 磁盘有文件但 DB 未记（scanner 前的兜底）→ 仍 400
    _mk_script(root, "ghost/hidden.py")
    r = c.delete("/api/scripts/dirs", params={"path": "ghost"})
    assert r.status_code == 400

    # 空目录内的空子目录不影响删除外层？——不：磁盘非空一律拒绝（先清空语义）
    c.post("/api/scripts/dirs", json={"path": "half/inner"})
    r = c.delete("/api/scripts/dirs", params={"path": "half"})
    assert r.status_code == 400
    r = c.delete("/api/scripts/dirs", params={"path": "half/inner"})
    assert r.status_code == 200


def test_delete_traversal_rejected(isolated):
    c = TestClient(isolated["app"])
    for bad in ("..", "../escape", "/etc", "a//b", ""):
        r = c.delete("/api/scripts/dirs", params={"path": bad})
        assert r.status_code == 400, (bad, r.status_code, r.text)
