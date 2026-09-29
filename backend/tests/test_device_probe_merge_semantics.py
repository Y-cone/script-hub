"""批次 AL：probe 缓存的**合并写入**语义 + /{id}/probe 入缓存（离线断言，不联网、不建真库）。

背景：cache_probe 此前是整体覆写，三个写入点语义不同 → 有的把 latency 冲成 None，
有的把「连不上」写成一条看起来正常的结论。AL 之后规则只有两条：

  · ok=True  → 逐字段合并：本次**没探**的项（参数为 None）保留旧值，显式传值（含 []）覆盖。
  · ok=False → 这台机器现在够不着 → **整条结论作废**（os_info/latency/runtimes 全清），只留 error。

本文件锁死五件事（跑 `-q -s` 可见每步的原始 JSON 证据）：
  1. 连接失败（ok=False）后 runtimes/os_info/latency **不保留旧值** —— 不许出现
     「设备连不上，UI 却说装了 bash 所以能跑」。
  2. test 成功（带 runtimes）→ 列表接口读得到该 runtimes。
  3. GET /{id}/probe 也写 cache → 列表接口读得到它探到的 runtimes，**且 latency_ms 没被冲成 None**。
  4. /{id}/probe 失败（502）时也写 ok=False（与 /test 同口径），旧结论作废。
  5. 平台探测失败（remote_probe 返回 ok=False，runtimes 里全是失败条目）**不是结论**，
     不许把那一堆 installed=false 当「确定没装」写进缓存；旧结论保留（可达即不作废）。

跑法：cd backend && .venv/bin/python -m pytest tests/test_device_probe_merge_semantics.py -q -s
隔离：DATA_DIR / 脚本根 / OS 配置目录全指向 tmp_path；build_client 与 remote_probe 均被替换，
      另在连接池**类**上挂兜网（见 _no_net），任何真连 SSH 的路径立刻炸在测试里。
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
    """现取活模块（其它测试的 isolated fixture 会 del sys.modules['app.*']，模块顶部
    `import … as x` 拿到的是死对象，补丁打上去等于没打）。"""
    return importlib.import_module("app.routers.device")


def _cache():
    """probe 缓存本体（批次 AM 起住在 services/ssh_service；device 路由 import 的是同一个
    对象，所以往这里写/改 ts，列表接口那边立刻看得到）。"""
    return importlib.import_module("app.services.ssh_service")._probe_cache


def _no_net(monkeypatch):
    """兜网：没盖住的路一旦去取连接池 → 立刻炸在测试里，而不是真去连设备。"""
    ssh = importlib.import_module("app.services.ssh_service")

    async def boom(device):
        raise AssertionError(f"测试试图建立 SSH 连接（host={device.host!r}）——mock 没盖住")

    monkeypatch.setattr(type(ssh.pool), "get", boom)   # 打类，不打实例


class _StubClient:
    def close(self):
        pass


def _mock_client(monkeypatch, exc=None):
    """替换**路由器模块**上的 build_client（device.py 是 from…import 引进来的名字，
    补丁必须打在该模块属性上）。exc 给定时模拟握手失败。"""
    r = _router()
    if exc is None:
        monkeypatch.setattr(r, "build_client", lambda dev: _StubClient())
    else:
        def boom(dev):
            raise exc
        monkeypatch.setattr(r, "build_client", boom)


def _mock_probe(monkeypatch, payload):
    r = _router()

    async def fake_probe(dev):
        return payload

    monkeypatch.setattr(r, "remote_probe", fake_probe)


BASH_OK = {"name": "bash", "installed": True, "version": "5.2.26(1)-release"}
BASH_NO = {"name": "bash", "installed": False, "version": "not found"}
WIN_WITH_BASH = [{"name": "python", "installed": False, "version": "not found"},
                 {"name": "powershell", "installed": True, "version": "5.1.22621"},
                 BASH_OK]
WIN_NO_BASH = [{"name": "python", "installed": True, "version": "Python 3.12.3"},
               {"name": "powershell", "installed": True, "version": "5.1.22621"},
               BASH_NO]
# remote_probe 首探就失败时的形态：平台回落 unix，每条运行时都是「探测失败」条目 —— 不是结论
PROBE_FAILED_GARBAGE = [{"name": n, "installed": False, "version": "探测失败: timed out"}
                        for n in ("uname", "python", "python3", "node", "bash")]


def _probe(runtimes, os_info="Windows_NT", ok=True, error=None, platform="win32"):
    return {"platform": platform, "os_info": os_info, "runtimes": runtimes, "ok": ok, "error": error}


async def _mk_device(client, name, host):
    r = await client.post("/api/devices", json={
        "name": name, "type": "windows", "host": host, "username": "u", "auth_type": "password",
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _row(client, device_id):
    r = await client.get("/api/devices")
    assert r.status_code == 200, r.text
    row = next(d for d in r.json() if d["id"] == device_id)
    return {k: row[k] for k in ("id", "online", "os_info", "latency_ms", "last_error", "runtimes")}


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


def test_cache_probe_merge_grid(isolated):
    """① write-point 逐格表：直接调 cache_probe，读缓存原文（不经过 HTTP）。"""
    r = _router()
    d = 901
    rows: dict = {}

    def snap(tag):
        c = dict(_cache()[d])
        c.pop("ts", None)          # ts 每次都刷新，与语义无关
        rows[tag] = c

    r.cache_probe(d, ok=True, os_info="Windows_NT", latency_ms=12, runtimes=WIN_WITH_BASH,
                  platform="win32")
    snap("1_test_success_full")

    # ② /{id}/probe 成功：不给 latency_ms（它不测握手）→ 旧的 12 必须留着
    r.cache_probe(d, ok=True, os_info="Windows_NT", runtimes=WIN_NO_BASH)
    snap("2_probe_endpoint_success")

    # ③ 平台探测失败（test 里 remote_probe 异常/ok=False）：只刷 latency，结论不动
    r.cache_probe(d, ok=True, latency_ms=8)
    snap("3_platform_probe_failed")

    # ④ 连接失败：整条结论作废
    r.cache_probe(d, ok=False, error="Authentication failed")
    snap("4_connect_failed")

    # ⑤ 过期条目不给合并续命（把 ts 往回拨 700s > CACHE_TTL，批次 AM 起 600）
    r.cache_probe(d, ok=True, os_info="Windows_NT", latency_ms=5, runtimes=WIN_WITH_BASH,
                  platform="win32")
    _cache()[d]["ts"] -= 700
    r.cache_probe(d, ok=True, latency_ms=6)         # 平台探测失败路径
    snap("5_merge_from_expired")

    print("\n── cache_probe 逐格表（缓存原文，ts 已剔除）──")
    for k in sorted(rows):
        print(f"[{k}] {json.dumps(rows[k], ensure_ascii=False)}")

    # 批次 AO：platform 与 os_info/runtimes 同一套合并语义（同探同清、过期不续命）
    assert rows["1_test_success_full"] == {"ok": True, "os_info": "Windows_NT",
                                          "latency_ms": 12, "error": None, "runtimes": WIN_WITH_BASH,
                                          "platform": "win32"}
    assert rows["2_probe_endpoint_success"] == {"ok": True, "os_info": "Windows_NT",
                                                "latency_ms": 12,          # ★ 没被冲成 None
                                                "error": None, "runtimes": WIN_NO_BASH,
                                                "platform": "win32"}       # ★ 本次没探 → 留着
    assert rows["3_platform_probe_failed"] == {"ok": True, "os_info": "Windows_NT",
                                               "latency_ms": 8, "error": None,
                                               "runtimes": WIN_NO_BASH,    # 旧结论保留
                                               "platform": "win32"}
    assert rows["4_connect_failed"] == {"ok": False, "os_info": None, "latency_ms": None,
                                        "error": "Authentication failed", "runtimes": None,
                                        "platform": None}  # ★ 全清
    assert rows["5_merge_from_expired"]["runtimes"] is None                    # ★ 过期不续命
    assert rows["5_merge_from_expired"]["platform"] is None                    # ★ 同上
    assert rows["5_merge_from_expired"]["latency_ms"] == 6


def test_endpoint_paths_share_one_cache(isolated, monkeypatch):
    """② 端到端：/test 与 /{id}/probe 写同一份缓存，且失败会作废旧结论。"""
    _no_net(monkeypatch)
    rows: dict = {}

    async def scenario(client):
        a = await _mk_device(client, "a", "10.0.1.11")
        b = await _mk_device(client, "b", "10.0.1.12")

        # ① /test 成功（完整探测）→ 列表读得到 runtimes / os_info / latency
        _mock_client(monkeypatch)
        _mock_probe(monkeypatch, _probe(WIN_WITH_BASH))
        res = await client.post(f"/api/devices/{a}/test")
        assert res.status_code == 200 and res.json()["ok"] is True, res.text
        rows["1_test_ok"] = await _row(client, a)
        assert rows["1_test_ok"]["runtimes"] == WIN_WITH_BASH
        assert rows["1_test_ok"]["os_info"] == "Windows_NT"
        latency_after_test = rows["1_test_ok"]["latency_ms"]
        assert isinstance(latency_after_test, int)

        # ② GET /{id}/probe 成功 → 也写缓存；列表能看到它探到的 runtimes，latency 不被冲掉
        #   （探测端点不测握手耗时 → 合并语义下必须原样保留；先把值改成 42，好让证据一眼可辨）
        _cache()[a]["latency_ms"] = 42
        _mock_probe(monkeypatch, _probe(WIN_NO_BASH))
        res = await client.get(f"/api/devices/{a}/probe")
        assert res.status_code == 200, res.text
        assert set(res.json()) == {"name", "type", "host", "platform", "os", "port", "runtimes"}
        assert res.json()["runtimes"] == WIN_NO_BASH
        rows["2_probe_endpoint_ok"] = await _row(client, a)
        assert rows["2_probe_endpoint_ok"]["runtimes"] == WIN_NO_BASH       # ★ 探到了就入缓存
        assert rows["2_probe_endpoint_ok"]["latency_ms"] == 42             # ★ 没被冲成 None
        assert rows["2_probe_endpoint_ok"]["os_info"] == "Windows_NT"

        # ③ /{id}/probe 首探失败 → 502，且缓存按「够不着」处理：旧 runtimes/os_info 作废
        _mock_probe(monkeypatch, _probe([], ok=False, error="Authentication failed", os_info=""))
        res = await client.get(f"/api/devices/{a}/probe")
        assert res.status_code == 502, res.text
        rows["3_probe_endpoint_fail"] = await _row(client, a)
        assert rows["3_probe_endpoint_fail"]["online"] is False
        assert rows["3_probe_endpoint_fail"]["last_error"] == "Authentication failed"
        assert rows["3_probe_endpoint_fail"]["runtimes"] is None            # ★ 连不上就不许说装了 bash
        assert rows["3_probe_endpoint_fail"]["os_info"] is None

        # ④ 恢复后再 /test → 结论回来
        _mock_probe(monkeypatch, _probe(WIN_WITH_BASH))
        await client.post(f"/api/devices/{a}/test")
        rows["4_recovered"] = await _row(client, a)
        assert rows["4_recovered"]["runtimes"] == WIN_WITH_BASH
        assert rows["4_recovered"]["os_info"] == "Windows_NT"

        # ⑤ 握手失败（/test 第一条写入点）→ 旧 runtimes 必须作废
        _mock_client(monkeypatch, exc=TimeoutError("Connection timed out"))
        res = await client.post(f"/api/devices/{a}/test")
        assert res.status_code == 200 and res.json()["ok"] is False, res.text
        rows["5_handshake_failed"] = await _row(client, a)
        assert rows["5_handshake_failed"]["online"] is False
        assert rows["5_handshake_failed"]["runtimes"] is None               # ★ 核心回归点
        assert rows["5_handshake_failed"]["os_info"] is None
        assert rows["5_handshake_failed"]["latency_ms"] is None

        # ⑥ 平台探测失败（remote_probe 返回 ok=False + 全 installed=false 的失败条目）
        #    → 那一堆条目不是结论，不许写成「确定没装 bash」；设备可达，旧结论保留
        _mock_client(monkeypatch)
        _mock_probe(monkeypatch, _probe(WIN_NO_BASH))
        await client.post(f"/api/devices/{b}/test")
        rows["6a_before_platform_fail"] = await _row(client, b)
        assert rows["6a_before_platform_fail"]["runtimes"] == WIN_NO_BASH

        _mock_probe(monkeypatch, _probe(PROBE_FAILED_GARBAGE, ok=False,
                                        error="timed out", os_info="探测失败: timed out"))
        res = await client.post(f"/api/devices/{b}/test")
        assert res.status_code == 200 and res.json()["ok"] is True, res.text
        assert "平台探测失败" in res.json()["message"]
        rows["6b_platform_probe_failed"] = await _row(client, b)
        assert rows["6b_platform_probe_failed"]["runtimes"] == WIN_NO_BASH  # ★ 垃圾条目没覆盖旧结论
        assert rows["6b_platform_probe_failed"]["os_info"] == "Windows_NT"  # ★ 也没被冲成 None
        assert rows["6b_platform_probe_failed"]["latency_ms"] is not None

        # ⑦ 旧结论已过期（> TTL）时，同样的「平台探测失败」不许把它续命
        _cache()[b]["ts"] -= 700
        await client.post(f"/api/devices/{b}/test")
        rows["7_platform_fail_after_expiry"] = await _row(client, b)
        assert rows["7_platform_fail_after_expiry"]["runtimes"] is None     # ★ 过期不续命
        assert rows["7_platform_fail_after_expiry"]["online"] is True
        return rows

    rows = _run(scenario)
    print("\n── GET /api/devices 原始片段（证据）──")
    for k in sorted(rows):
        print(f"[{k}] {json.dumps(rows[k], ensure_ascii=False)}")
