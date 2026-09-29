"""批次 AO ③：**设备上下文下**的环境检测（离线断言，不联网、不建真库）。

背景：`GET /api/scripts/{id}/env-check` 此前不接设备参数，`check_environment` 也只看得到
Server 本机平台（`current_platform()` / `shutil.which`）。于是在 Windows 设备的脚本工作区点
「环境检测」→ 探到 Server 本机的 Linux → 平台检查项报 `unix` → `.sh` 被误判不通过（用户报的
第 ③ 个问题）。数据其实早就有：`ssh_service.remote_probe` 探的就是目标机，probe 缓存
（`_probe_cache` / `fresh_entry`，批次 AK/AL/AM）里躺着 platform + runtimes。

本文件锁死四件事：
  1. **默认行为不变**：不传设备参数 → 判据仍是 Server 本机（`run.py` 的 `if not device:` 分支靠它）；
  2. **设备上下文判远端**：`device_platform` / `device_runtimes` 决定平台检查项与替代运行时
     （`.sh` 在 Windows 靠 bash）判据，**绝不回落** `current_platform()` / `shutil.which`；
  3. **无数据 ≠ 没装**：远端拿不到结论（平台未知 / runtimes 没这一项 / 缓存为空）→ 不判红，
     且响应能用 `conclusive` 把「有结论」和「无数据」分开；
  4. **端点端到端**：Windows 设备 + 装了 bash → 平台检查项 `actual="windows"`、`.sh` 经
     `ALT_RUNTIME_RULES` 判为可跑；不带 `device_id` → 仍是本机 unix。

跑法：cd backend && .venv/bin/python -m pytest tests/test_envcheck_device_context.py -q -s
隔离：DATA_DIR / 脚本根 / OS 配置目录全指向 tmp_path；`build_client` 与 `remote_probe` 均被替换，
      另在连接池**类**上挂兜网（见 _no_net），任何真连 SSH 的路径立刻炸在测试里而不是打到真设备。
"""
import asyncio
import importlib
import json
import sys
from pathlib import Path

import httpx
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """隔离 DATA_DIR / 脚本根 / OS 配置目录，再导入 app（config.py 在 import 时解析路径）。"""
    data = tmp_path / "data"
    root = tmp_path / "scripts"
    monkeypatch.setenv("SCRIPTHUB_DATA_DIR", str(data))
    monkeypatch.setenv("SCRIPTHUB_SCRIPTS_ROOT", str(root))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfghome"))
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]
    data.mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    yield {"root": root, "data": data}
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]


def _router():
    """现取活模块：别的测试的 isolated fixture 会 `del sys.modules['app.*']`，
    模块顶部 `import … as x` 拿到的对象随后就成了死对象（补丁打在死对象上等于没打）。"""
    return importlib.import_module("app.routers.device")


def _envcheck():
    return importlib.import_module("app.services.envcheck")


def _cache():
    """probe 缓存本体（批次 AM 起住在 services/ssh_service）。"""
    return importlib.import_module("app.services.ssh_service")._probe_cache


def _no_net(monkeypatch):
    """兜网：没盖住的路一旦去取连接池 → 立刻炸在测试里，而不是真去连设备。"""
    ssh = importlib.import_module("app.services.ssh_service")

    async def boom(device):
        raise AssertionError(f"测试试图建立 SSH 连接（host={device.host!r}）——mock 没盖住")

    monkeypatch.setattr(type(ssh.pool), "get", boom)


class _StubClient:
    def close(self):
        pass


def _mock_client(monkeypatch):
    """替换**路由器模块**上的 build_client（device.py 是 from…import 引进来的名字）。"""
    monkeypatch.setattr(_router(), "build_client", lambda dev: _StubClient())


def _mock_probe(monkeypatch, payload):
    async def fake_probe(dev):
        return payload

    monkeypatch.setattr(_router(), "remote_probe", fake_probe)


BASH_OK = {"name": "bash", "installed": True, "version": "5.2.26(1)-release"}
BASH_NO = {"name": "bash", "installed": False, "version": "not recognized"}
WIN_RUNTIMES_BASH = [{"name": "python", "installed": True, "version": "Python 3.12.3"},
                     {"name": "powershell", "installed": True, "version": "5.1.22621"},
                     BASH_OK]
WIN_RUNTIMES_NO_BASH = [{"name": "python", "installed": True, "version": "Python 3.12.3"},
                        {"name": "powershell", "installed": True, "version": "5.1.22621"},
                        BASH_NO]


def _probe(runtimes, os_info="Windows_NT", ok=True, error=None, platform="win32"):
    return {"platform": platform, "os_info": os_info, "runtimes": runtimes,
            "ok": ok, "error": error}


def _script(category: str, name: str, env_requests: str | None = None):
    """内存态 Script（不落库；判据只读 category / name / env_requests）。"""
    from app.models.script import Script
    return Script(id=1, name=name, path=f"/nonexistent/{name}", relative_path=name,
                  extension=Path(name).suffix, category=category, env_requests=env_requests)


def _run(scenario):
    async def body():
        import app.models  # noqa: F401 — 注册全部 ORM 模型，create_all 才有表可建
        from app.database import init_db
        from app.main import app
        await init_db()
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        try:
            return await scenario(client)
        finally:
            await client.aclose()

    return asyncio.run(body())


async def _mk_device(client, name="win-box"):
    r = await client.post("/api/devices", json={
        "name": name, "type": "windows", "host": "10.0.0.9", "username": "u",
        "auth_type": "password",
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _mk_scripts(client, root: Path, *files: str) -> dict[str, int]:
    """把脚本站不住的真实文件写进隔离脚本根，再走真实扫描入库（category 由扩展名定）。"""
    for f in files:
        (root / f).write_text("#!/bin/bash\necho hi\n", encoding="utf-8")
    r = await client.post("/api/scripts/scan")
    assert r.status_code == 200, r.text
    r = await client.get("/api/scripts", params={"page_size": 100})
    assert r.status_code == 200, r.text
    return {s["name"]: s["id"] for s in r.json()["items"]}


def _checks_by_type(payload, typ="platform"):
    return [c for c in payload["checks"] if c["type"] == typ]


# ── ① 默认行为不变：不传设备参数 = 判 Server 本机 ──────────────────────────────────────

def test_local_default_unchanged_by_new_params(isolated):
    """不传 device_* → 仍是本机判据（run.py 的本机前置靠这条）；新增参数不影响它。"""
    ec = _envcheck()
    here = ec.current_platform()

    sh = ec.check_environment(_script("shell", "a.sh"))
    platform_check = _checks_by_type({"checks": sh})[0]
    assert platform_check["actual"] == here, platform_check
    assert platform_check["ok"] is (here == "unix"), platform_check

    bat = ec.check_environment(_script("bat", "a.bat"))
    bat_platform = _checks_by_type({"checks": bat})[0]
    assert bat_platform["actual"] == here
    assert bat_platform["ok"] is (here == "windows")
    # 本机路径照旧靠 shutil.which：Linux 上没 bash 才会变红 —— 这里只要求与 has_runtime 一致
    assert ("未检测到 bash" in bat_platform["detail"]) is (not ec.has_runtime("bash"))


# ── ② 远端判据：平台 + 替代运行时都看设备数据 ─────────────────────────────────────────

def test_remote_platform_uses_device_not_server(isolated):
    """Windows 设备上的 .sh：平台检查项报 windows（不再报 Server 本机的 unix）。"""
    ec = _envcheck()
    checks = ec.check_environment(_script("shell", "gateway.sh"),
                                  device_platform="win32",
                                  device_runtimes=WIN_RUNTIMES_BASH)
    platform_check = checks[0]
    assert platform_check["actual"] == "windows", platform_check
    assert platform_check["ok"] is True, platform_check
    assert "经 bash 运行" in platform_check["detail"], platform_check


def test_remote_alt_runtime_three_states(isolated):
    """`.sh` 在 Windows 的替代运行时三态：装了 → 绿；**确定没装** → 红；没探到 → 不判红。"""
    ec = _envcheck()
    sh = _script("shell", "gateway.sh")

    with_bash = ec.check_environment(sh, device_platform="win32", device_runtimes=WIN_RUNTIMES_BASH)[0]
    assert with_bash["ok"] is True and "经 bash 运行" in with_bash["detail"], with_bash

    no_bash = ec.check_environment(sh, device_platform="win32", device_runtimes=WIN_RUNTIMES_NO_BASH)[0]
    assert no_bash["ok"] is False, no_bash                       # installed=false = 确定没装 → 该红
    assert "未检测到 bash" in no_bash["detail"], no_bash

    no_entry = ec.check_environment(sh, device_platform="win32", device_runtimes=[])[0]
    assert no_entry["ok"] is True, no_entry                      # 无结论 ≠ 没装 → 不判红
    assert "未取得 bash 的探测结论" in no_entry["detail"], no_entry

    # 平台未知（探测半途失败）→ 无结论，且不许回落成 Server 本机平台
    unknown = ec.check_environment(sh, device_platform=None, device_runtimes=[])[0]
    assert unknown["ok"] is True and unknown["actual"] is None, unknown
    assert "未取得目标设备的平台结论" in unknown["detail"], unknown


def test_remote_runtime_requirements_use_device_probe(isolated):
    """env_requests 的运行时判据：远端读设备探测结论，缺条目 → 无结论（不判红）。"""
    ec = _envcheck()
    ps = _script("powershell", "switch.ps1", env_requests='{"powershell": ">=5"}')
    checks = [c for c in ec.check_environment(
        ps, device_platform="win32", device_runtimes=WIN_RUNTIMES_BASH) if c["type"] == "runtime"]
    assert len(checks) == 1, checks
    assert checks[0]["ok"] is True and checks[0]["actual"] == "5.1.22621", checks

    # 设备探测结论里没有 java（probe 不覆盖它）→ 无结论 → 不判红、不编造版本
    java = _script("powershell", "switch.ps1", env_requests='{"java": ">=17"}')
    jc = [c for c in ec.check_environment(
        java, device_platform="win32", device_runtimes=WIN_RUNTIMES_BASH) if c["type"] == "runtime"]
    assert jc[0]["ok"] is True and jc[0]["actual"] is None, jc
    assert "无结论" in jc[0]["detail"], jc


# ── ③ 端点端到端：?device_id=X 走设备上下文 ───────────────────────────────────────────

def test_env_check_endpoint_device_context(isolated, monkeypatch):
    """Windows 设备（缓存里装了 bash）→ 平台项 windows、`.sh` 判可跑；不带 device_id → 仍 unix。"""
    _no_net(monkeypatch)
    _mock_client(monkeypatch)
    _mock_probe(monkeypatch, _probe(WIN_RUNTIMES_BASH))
    printed: dict = {}

    async def scenario(client):
        dev = await _mk_device(client)
        ids = await _mk_scripts(client, isolated["root"], "Hermes_SwitchGW.sh", "cleanup.bat")
        sh_id, bat_id = ids["Hermes_SwitchGW.sh"], ids["cleanup.bat"]

        # 设备上下文：先按真实路径让 /test 写进 probe 缓存（模拟「连接设备时的自动探测」）
        r = await client.post(f"/api/devices/{dev}/test")
        assert r.status_code == 200, r.text
        assert _cache()[dev]["platform"] == "win32", _cache()[dev]

        r = await client.get(f"/api/scripts/{sh_id}/env-check", params={"device_id": dev})
        assert r.status_code == 200, r.text
        payload = r.json()
        printed["设备上下文 · .sh（Windows + bash）"] = payload
        assert payload["source"] == "device" and payload["conclusive"] is True, payload
        pc = payload["checks"][0]
        assert pc["actual"] == "windows", pc                 # ★ 用户报的就是这条报 unix
        assert pc["ok"] is True and "经 bash 运行" in pc["detail"], pc
        assert payload["all_ok"] is True, payload

        r = await client.get(f"/api/scripts/{bat_id}/env-check", params={"device_id": dev})
        bc = r.json()
        printed["设备上下文 · .bat（Windows）"] = bc
        assert bc["checks"][0]["actual"] == "windows" and bc["checks"][0]["ok"] is True, bc

        # 反向：同一脚本不带 device_id = 本机判据（行为未变）
        r = await client.get(f"/api/scripts/{sh_id}/env-check")
        local = r.json()
        printed["不带 device_id（本机）"] = local
        assert local["source"] == "local" and local["device_id"] is None, local
        assert local["checks"][0]["actual"] == _envcheck().current_platform(), local
        assert local["checks"][0]["actual"] != "windows", local
        return printed

    out = _run(scenario)
    print("\n── env-check 响应原文 ──")
    for k, v in out.items():
        print(f"[{k}] {json.dumps(v, ensure_ascii=False)}")


def test_env_check_endpoint_no_data_is_not_red(isolated, monkeypatch):
    """③ 缺数据：设备存在但缓存空 → 明确「无探测结论」（conclusive=false），一项都不判红。"""
    _no_net(monkeypatch)
    printed: dict = {}

    async def scenario(client):
        dev = await _mk_device(client, "never-probed")
        ids = await _mk_scripts(client, isolated["root"], "gateway.sh")
        sh_id = ids["gateway.sh"]

        # (a) 从未探测过 → 无条目
        r = await client.get(f"/api/scripts/{sh_id}/env-check", params={"device_id": dev})
        assert r.status_code == 200, r.text
        empty = r.json()
        printed["(a) 从未探测过"] = empty
        assert empty["conclusive"] is False, empty
        assert empty["checks"] == [] and empty["unmet"] == [], empty      # ★ 一项都不判红（无检查项）
        assert "没有未过期的环境探测结论" in empty["note"], empty

        # (b) 探到平台、但 runtimes 没拿到（探过 ≠ 有结论）→ 平台项有结论，`.sh` 项不判红
        from app.services.ssh_service import cache_probe
        cache_probe(dev, ok=True, os_info="Windows_NT", platform="win32")
        r = await client.get(f"/api/scripts/{sh_id}/env-check", params={"device_id": dev})
        partial = r.json()
        printed["(b) 有平台、无 runtimes"] = partial
        assert partial["conclusive"] is True, partial
        assert partial["checks"][0]["actual"] == "windows", partial
        assert all(c["ok"] for c in partial["checks"]), partial          # ★ 仍是「不判红」

        # (c) 上次探测连不上（ok=false）→ 旧结论作废，note 带失败原因
        cache_probe(dev, ok=False, error="Authentication failed")
        r = await client.get(f"/api/scripts/{sh_id}/env-check", params={"device_id": dev})
        failed = r.json()
        printed["(c) 上次探测失败"] = failed
        assert failed["conclusive"] is False and failed["checks"] == [], failed
        assert "Authentication failed" in failed["note"], failed
        return printed

    out = _run(scenario)
    print("\n── env-check「无数据」三种形态 ──")
    for k, v in out.items():
        print(f"[{k}] {json.dumps(v, ensure_ascii=False)}")


def test_env_check_endpoint_unknown_device_404(isolated):
    """device_id 不在库 → 404（不许静默回落本机 —— 那正是本批次要修的错判）。"""
    async def scenario(client):
        ids = await _mk_scripts(client, isolated["root"], "gateway.sh")
        r = await client.get(f"/api/scripts/{ids['gateway.sh']}/env-check", params={"device_id": 999})
        assert r.status_code == 404, r.text
        return r.json()

    print("\n── 不存在的 device_id ──", _run(scenario))
