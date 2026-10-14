"""POST /api/scripts/dirs 新建目录：路径穿越拒绝 + 正常创建幂等（含空目录进 GET /dirs）。

跑法：cd backend && .venv/bin/python -m pytest tests/test_dirs_create.py -q
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """隔离 DATA_DIR 后导入 app（config.py 在 import 时解析路径），同 test_settings.py。"""
    monkeypatch.setenv("SCRIPTHUB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfghome"))
    monkeypatch.delenv("SCRIPTHUB_SCRIPTS_ROOT", raising=False)
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]
    from app.main import app  # noqa: F401
    from app import config
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / "scripts").mkdir(exist_ok=True)
    yield {"app": app, "config": config, "tmp": tmp_path}
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]


def test_create_dir_rejects_traversal_and_creates_idempotent(isolated):
    c = TestClient(isolated["app"])
    root = isolated["config"].DATA_DIR / "scripts"

    # 穿越/非法：绝对路径、..、空串 → 400，且根目录外没有落盘
    for bad in ("/etc", "C:/Windows", "../../escape", "a/../../b", "a/../..", "..", ".", "", "  ", "a//b", "a/b/"):
        r = c.post("/api/scripts/dirs", json={"path": bad})
        assert r.status_code == 400, (bad, r.status_code, r.text)
    assert not (root.parent / "escape").exists()
    assert list(root.iterdir()) == []

    # 正常：多级创建（mkdir -p）
    r = c.post("/api/scripts/dirs", json={"path": "a/b/c"})
    assert r.status_code == 200
    assert (root / "a" / "b" / "c").is_dir()

    # 幂等：重复创建仍 200（目录内容不变）
    r = c.post("/api/scripts/dirs", json={"path": "a/b/c"})
    assert r.status_code == 200

    # 新建的空目录立即可见于 GET /dirs（否则前端刷新后树里不出现）
    dirs = c.get("/api/scripts/dirs").json()["directories"]
    assert dirs == ["a", "a/b", "a/b/c"]
