"""secret_store 路径隔离测试（H-2）。

覆盖：默认路径不变 / XDG_CONFIG_HOME 生效 / SCRIPTHUB_SECRETS_FILE 优先级最高 /
写盘只落隔离路径且 0600（目录 0700）。全部走 tmp_path，绝不触碰真实 ~/.config。

跑法：cd backend && .venv/bin/python -m pytest tests/test_secret_store_path.py -q
"""
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

DEFAULT = Path.home() / ".config" / "scripthub" / "secrets.json"


def _load(monkeypatch, *, secrets_file, xdg):
    """按给定 env 重新导入 secret_store（模块级解析路径）。"""
    for name, val in (("SCRIPTHUB_SECRETS_FILE", secrets_file), ("XDG_CONFIG_HOME", xdg)):
        if val is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, val)
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]
    from app.services import secret_store

    monkeypatch.setattr(secret_store, "_keyring", None, raising=False)  # 只验文件路径，避开系统钥匙串
    return secret_store


def test_default_path_unchanged(monkeypatch):
    s = _load(monkeypatch, secrets_file=None, xdg=None)
    assert s._SECRETS_FILE == DEFAULT
    assert s._SECRETS_DIR == DEFAULT.parent


def test_xdg_config_home_isolates(monkeypatch, tmp_path):
    s = _load(monkeypatch, secrets_file=None, xdg=str(tmp_path / "cfghome"))
    assert s._SECRETS_FILE == tmp_path / "cfghome" / "scripthub" / "secrets.json"


def test_explicit_file_beats_xdg(monkeypatch, tmp_path):
    s = _load(
        monkeypatch,
        secrets_file=str(tmp_path / "explicit.json"),
        xdg=str(tmp_path / "cfghome"),
    )
    assert s._SECRETS_FILE == tmp_path / "explicit.json"


def test_write_lands_in_isolated_path_only(monkeypatch, tmp_path):
    s = _load(monkeypatch, secrets_file=None, xdg=str(tmp_path / "cfghome"))
    before = DEFAULT.stat().st_mtime_ns if DEFAULT.exists() else None

    assert s.set_secret("svc-test", "user-test", "not-a-real-secret") is False

    target = tmp_path / "cfghome" / "scripthub" / "secrets.json"
    assert target.exists(), f"凭据未落到隔离路径: {s._SECRETS_FILE}"
    assert s.get_secret("svc-test", "user-test") == "not-a-real-secret"

    assert oct(target.stat().st_mode & 0o777) == "0o600"
    assert oct(target.parent.stat().st_mode & 0o777) == "0o700"
    # 真实凭据文件未被触碰
    if before is not None:
        assert DEFAULT.stat().st_mtime_ns == before

    s.delete_secret("svc-test", "user-test")
    assert s.get_secret("svc-test", "user-test") is None
