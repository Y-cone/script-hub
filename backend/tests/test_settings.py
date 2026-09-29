"""设置面板后端测试（SPEC §2.9 / §7.1 / §7.8）。

跑法：cd backend && .venv/bin/python -m pytest tests/test_settings.py -q
"""
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
    from app.main import app  # noqa: F401
    from app import config

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / "scripts").mkdir(exist_ok=True)
    # 种一个空 db，模拟"当前数据目录有库可迁移"
    (config.DATA_DIR / "scripthub.db").write_bytes(b"")
    yield {"app": app, "config": config, "tmp": tmp_path}
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]


def client(isolated):
    return TestClient(isolated["app"])


def test_validate_three_states(isolated):
    c = client(isolated)
    tmp = isolated["tmp"]
    existing = tmp / "has_data"
    existing.mkdir()
    (existing / "scripthub.db").write_bytes(b"x" * 1024)
    empty_dir = tmp / "empty_dir"
    empty_dir.mkdir()
    missing = tmp / "no" / "such"
    r = c.post("/api/settings/validate", json={
        "data_dir": str(existing),
        "scripts_root": str(empty_dir),
    })
    assert r.status_code == 200
    body = r.json()
    # 存在且有库
    assert body["data_dir"]["exists"] is True
    assert body["data_dir"]["writable"] is True
    assert body["data_dir"]["empty"] is False
    assert body["data_dir"]["size_mb"] >= 0.0
    # 存在但空 → empty=true（warn 档），仍可写
    assert body["scripts_root"]["exists"] is True
    assert body["scripts_root"]["empty"] is True
    assert body["scripts_root"]["script_count"] == 0
    assert body["scripts_root"]["dir_count"] == 0
    # 不存在且父目录可写 → error 为空但 exists=false（可创建）
    r2 = c.post("/api/settings/validate", json={"data_dir": str(missing)})
    d = r2.json()["data_dir"]
    assert d["exists"] is False
    assert d["error"] is None
    # 不存在且父目录不可写 → err
    readonly_root = tmp / "ro"
    readonly_root.mkdir()
    os.chmod(readonly_root, 0o555)
    try:
        r3 = c.post("/api/settings/validate", json={"data_dir": str(readonly_root / "sub")})
        assert r3.json()["data_dir"]["error"]
    finally:
        os.chmod(readonly_root, 0o755)


def test_validate_fields_independent(isolated):
    c = client(isolated)
    bad = isolated["tmp"] / "ro_parent"
    bad.mkdir()
    os.chmod(bad, 0o555)
    good = isolated["tmp"] / "good_root"
    good.mkdir()
    try:
        r = c.post("/api/settings/validate", json={
            "data_dir": str(bad / "sub"),
            "scripts_root": str(good),
        })
        assert r.status_code == 200
        body = r.json()
        assert body["data_dir"]["error"]  # 非法
        assert body["scripts_root"]["exists"] is True  # 不被阻断
    finally:
        os.chmod(bad, 0o755)


def test_put_writes_settings_json_not_data_dir(isolated):
    c = client(isolated)
    cfg = isolated["config"]
    new_root = isolated["tmp"] / "new_scripts"
    new_root.mkdir()
    r = c.put("/api/settings", json={"scripts_root": str(new_root)})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["restart_required"] is True
    assert body["scripts_root"] == str(new_root)
    # settings.json 在 OS 配置目录，不在 data_dir 之下（§7.8-③）
    assert cfg.CONFIG_PATH.exists()
    assert cfg.DATA_DIR not in cfg.CONFIG_PATH.parents
    on_disk = json.loads(cfg.CONFIG_PATH.read_text())
    assert on_disk["script_root_dir"] == str(new_root)
    # 重新加载（模拟重启后 load_config）值已改
    assert cfg.load_config()["script_root_dir"] == str(new_root)


def test_put_invalid_path_400(isolated):
    c = client(isolated)
    ro = isolated["tmp"] / "ro2"
    ro.mkdir()
    os.chmod(ro, 0o555)
    try:
        r = c.put("/api/settings", json={"data_dir": str(ro / "sub"), "migrate": True})
        assert r.status_code == 400
        assert "detail" in r.json()
    finally:
        os.chmod(ro, 0o755)


def test_env_overrides_put_and_config(isolated, monkeypatch):
    c = client(isolated)
    cfg = isolated["config"]
    env_root = isolated["tmp"] / "env_scripts"
    put_root = isolated["tmp"] / "put_scripts"
    env_root.mkdir()
    put_root.mkdir()
    monkeypatch.setenv("SCRIPTHUB_SCRIPTS_ROOT", str(env_root))
    # 重新解析配置（env 在 load_config 内生效，无需重载 app）
    r = c.put("/api/settings", json={"scripts_root": str(put_root)})
    assert r.status_code == 200
    # v2.14：被 env 遮蔽的字段不得写盘（否则取消 env 后会静默用上脏值）
    on_disk = json.loads(cfg.CONFIG_PATH.read_text())
    assert str(put_root) not in json.dumps(on_disk)
    assert any("SCRIPTHUB_SCRIPTS_ROOT" in w for w in r.json()["warnings"])
    # 运行值 = env 值
    assert cfg.load_config()["script_root_dir"] == str(env_root)
    assert str(cfg.get_script_root()) == str(env_root)


def test_migrate_only_into_empty_target(isolated):
    c = client(isolated)
    cfg = isolated["config"]
    target = isolated["tmp"] / "target"
    target.mkdir()
    (target / "scripthub.db").write_bytes(b"existing")  # 非空目标
    new_root = target / "scripts"
    r = c.put("/api/settings", json={"data_dir": str(target), "migrate": True})
    assert r.status_code == 200
    body = r.json()
    assert body["migrated"] is False
    # isolated fixture 设了 SCRIPTHUB_DATA_DIR → data_dir 被遮蔽，写入动作照旧给迁移提示 + 遮蔽说明
    assert body["warnings"] == [
        "目标目录已有 scripthub.db，未迁移",
        "SCRIPTHUB_DATA_DIR 已设置，data_dir 以环境变量为准，未写入",
    ]
    assert (target / "scripthub.db").read_bytes() == b"existing"  # 未覆盖

    # 空目标 → 迁移执行，源不删除
    target2 = isolated["tmp"] / "target2"
    r2 = c.put("/api/settings", json={"data_dir": str(target2), "migrate": True})
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["migrated"] is True
    assert (target2 / "scripthub.db").exists()
    assert (cfg.DATA_DIR / "scripthub.db").exists()  # 原目录永不删除


def test_legacy_config_fallback(isolated, monkeypatch):
    """老用户兼容：新 settings.json 不存在时读旧 DATA_DIR/config.json。"""
    cfg = isolated["config"]
    legacy_root = isolated["tmp"] / "legacy_scripts"
    legacy_root.mkdir()
    # 不写 settings.json，只写旧位置
    (cfg.DATA_DIR / "config.json").write_text(json.dumps({"script_root_dir": str(legacy_root)}))
    loaded = cfg.load_config()
    assert loaded["script_root_dir"] == str(legacy_root)
    # 默认播种仍发生在旧位置（新装场景行为不变）
    assert not cfg.CONFIG_PATH.exists()
    # 保存后写到新位置，且优先于旧文件
    c = client(isolated)
    new_root = isolated["tmp"] / "after_save"
    new_root.mkdir()
    r = c.put("/api/settings", json={"scripts_root": str(new_root)})
    assert r.status_code == 200
    assert cfg.CONFIG_PATH.exists()
    assert cfg.load_config()["script_root_dir"] == str(new_root)


def test_put_shadowed_fields_not_persisted(isolated, monkeypatch):
    """A1：外部 env 存在时，PUT 的对应字段不得写盘，warnings 说明（复现 v2.14 缺陷）。"""
    c = client(isolated)
    cfg = isolated["config"]
    env_dir = isolated["tmp"] / "env_data"
    put_dir = isolated["tmp"] / "put_data"
    put_dir.mkdir()
    monkeypatch.setenv("SCRIPTHUB_DATA_DIR", str(cfg.DATA_DIR))  # 保持运行目录不变，仅制造遮蔽
    monkeypatch.setenv("SCRIPTHUB_SCRIPTS_ROOT", str(isolated["tmp"] / "env_scripts"))
    r = c.put("/api/settings", json={"data_dir": str(put_dir), "scripts_root": str(put_dir)})
    assert r.status_code == 200
    on_disk = json.loads(cfg.CONFIG_PATH.read_text())
    assert str(put_dir) not in json.dumps(on_disk)  # 脏值不得落盘
    warns = r.json()["warnings"]
    assert any("SCRIPTHUB_DATA_DIR" in w for w in warns)
    assert any("SCRIPTHUB_SCRIPTS_ROOT" in w for w in warns)


def test_system_info_readonly(isolated, monkeypatch):
    """A2：readonly = 外部覆盖变量是否存在（壳注入的默认变量不算）。"""
    c = client(isolated)
    monkeypatch.delenv("SCRIPTHUB_DATA_DIR", raising=False)
    monkeypatch.delenv("SCRIPTHUB_SCRIPTS_ROOT", raising=False)
    monkeypatch.setenv("SCRIPTHUB_DEFAULT_DATA_DIR", str(isolated["tmp"] / "shell_default"))
    monkeypatch.setenv("SCRIPTHUB_SCRIPTS_ROOT", "/tmp/x")
    body = c.get("/api/system/info").json()
    assert body["readonly"] == {"data_dir": False, "scripts_root": True}


def test_data_dir_priority(isolated, monkeypatch):
    """A3：SCRIPTHUB_DATA_DIR > settings.json 的 data_dir > SCRIPTHUB_DEFAULT_DATA_DIR > 仓库 data/。"""
    import importlib
    cfg_src = (Path(__file__).resolve().parents[1] / "app" / "config.py").read_text()
    assert "SCRIPTHUB_DEFAULT_DATA_DIR" in cfg_src  # 优先级链已实现

    tmp = isolated["tmp"]
    monkeypatch.delenv("SCRIPTHUB_DATA_DIR", raising=False)

    def resolve():
        for mod in list(sys.modules):
            if mod == "app" or mod.startswith("app."):
                del sys.modules[mod]
        import app.config as c2
        importlib.reload(c2)
        return c2

    # 第 3 优先：只有 SCRIPTHUB_DEFAULT_DATA_DIR → 用它
    default_dir = tmp / "dd"
    monkeypatch.setenv("SCRIPTHUB_DEFAULT_DATA_DIR", str(default_dir))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)  # 避免读到别的 settings.json
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp / "prio_cfg"))
    c3 = resolve()
    assert c3.DATA_DIR == default_dir

    # 第 2 优先：settings.json 有 data_dir → 压过 default
    saved_dir = tmp / "saved"
    c3.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    c3.CONFIG_PATH.write_text(json.dumps({"data_dir": str(saved_dir), "script_root_dir": str(saved_dir / "scripts")}))
    c3 = resolve()
    assert c3.DATA_DIR == saved_dir

    # 第 1 优先：SCRIPTHUB_DATA_DIR → 压过一切
    env_dir = tmp / "envd"
    monkeypatch.setenv("SCRIPTHUB_DATA_DIR", str(env_dir))
    c3 = resolve()
    assert c3.DATA_DIR == env_dir

    # 第 4 优先：全无 → 仓库 data/
    monkeypatch.delenv("SCRIPTHUB_DATA_DIR", raising=False)
    monkeypatch.delenv("SCRIPTHUB_DEFAULT_DATA_DIR", raising=False)
    (tmp / "prio_cfg" / "com.scripthub.app").mkdir(parents=True, exist_ok=True)  # 占位防播种
    (tmp / "prio_cfg" / "com.scripthub.app" / "settings.json").write_text("{}")
    c3 = resolve()
    assert c3.DATA_DIR == Path(__file__).resolve().parents[2] / "data"


def test_validate_size_only_core(isolated):
    """A4：size_mb 只算本体（db + runs/），不算全树；无 db → 无 size_mb。"""
    c = client(isolated)
    tmp = isolated["tmp"]
    # 大子目录不该计入（旧实现 rglob 全树会把它算进去）
    d = tmp / "sized"
    d.mkdir()
    (d / "scripthub.db").write_bytes(b"x" * 2048)
    (d / "runs").mkdir()
    (d / "runs" / "r1.txt").write_bytes(b"y" * 1024)
    (d / "huge_sub").mkdir()
    (d / "huge_sub" / "junk.bin").write_bytes(b"z" * 4096)
    body = c.post("/api/settings/validate", json={"data_dir": str(d)}).json()["data_dir"]
    assert body["size_mb"] == round((2048 + 1024) / 1048576, 1)
    # 无 db → 不给 size_mb，empty=true
    nodb = tmp / "nodb"
    nodb.mkdir()
    (nodb / "junk.bin").write_bytes(b"q" * 8192)
    body2 = c.post("/api/settings/validate", json={"data_dir": str(nodb)}).json()["data_dir"]
    assert "size_mb" not in body2
    assert body2["empty"] is True
