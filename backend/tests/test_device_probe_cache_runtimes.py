"""批次 AK①：设备 probe 缓存保留 runtimes + 三态语义（离线断言，不联网、不建真库）。

背景：`GET /api/devices`（列表）只回最近一次 test/probe 的**内存缓存**（SPEC §7.2-#2，绝不主动探测）。
缓存此前只有 online/latency/os_info → 前端渲染脚本列表时拿不到「这台远程机有没有 bash」。
本文件锁死三件事：
  1. test 端点探测成功后，runtimes **原文**确实进了缓存、并从 GET /api/devices 出来；
  2. 「从未探测过」与「探测了但没有 bash」**不同**（前者 runtimes=null，后者是条目 installed=false）；
     证据用返回 JSON 的原始片段打印出来（跑 `-s` 可见）。
  3. `[]`（探过但没拿到条目）既不是 None（未探测）、也不是「确定没装」——前端对两者都按「无结论」处理。

跑法：cd backend && .venv/bin/python -m pytest tests/test_device_probe_cache_runtimes.py -q -s
隔离：DATA_DIR / 脚本根 / OS 配置目录全指向 tmp_path；build_client 与 remote_probe 均被替换，
      另在连接池上挂兜网（见 _no_net），任何真连 SSH 的路径立刻失败而不是打到真设备。
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
    """现取活模块：其它测试的 isolated fixture 会 `del sys.modules['app.*']`，
    模块顶部 `import … as x` 拿到的对象随后就成了死对象，补丁打在死对象上等于没打。"""
    return importlib.import_module("app.routers.device")


def _cache():
    """probe 缓存本体（批次 AM 起住在 services/ssh_service；device 路由 import 的是同一个对象）。"""
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


def _mock_probe(monkeypatch, payload):
    """替换**路由器模块**上的 build_client / remote_probe（device.py 是 `from … import` 引进来的
    名字，补丁必须打在该模块的属性上；打 ssh_service 上的同名函数对它无效）。"""
    r = _router()
    monkeypatch.setattr(r, "build_client", lambda dev: _StubClient())

    async def fake_probe(dev):
        return payload

    monkeypatch.setattr(r, "remote_probe", fake_probe)


def _probe(runtimes):
    return {"platform": "win32", "os_info": "Windows_NT", "runtimes": runtimes,
            "ok": True, "error": None}


BASH_OK = {"name": "bash", "installed": True, "version": "5.2.26(1)-release"}
BASH_NO = {"name": "bash", "installed": False, "version": "探测失败: not found"}

WIN_WITH_BASH = [{"name": "python", "installed": False, "version": "not found"},
                 {"name": "powershell", "installed": True, "version": "5.1.22621"},
                 BASH_OK]
WIN_NO_BASH = [{"name": "python", "installed": True, "version": "Python 3.12.3"},
               {"name": "powershell", "installed": True, "version": "5.1.22621"},
               BASH_NO]


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
    return {k: row[k] for k in ("id", "name", "online", "os_info", "runtimes")}


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


def test_probe_cache_runtimes_three_states(isolated, monkeypatch):
    _no_net(monkeypatch)
    rows: dict = {}

    async def scenario(client):
        devs = {}
        for key, host in (("never", "10.0.0.11"), ("nobash", "10.0.0.12"),
                          ("withbash", "10.0.0.13"), ("empty", "10.0.0.14")):
            devs[key] = await _mk_device(client, key, host)

        # ① 从未探测过 → runtimes 为 None（**不是** []，否则前端会把「未知」当「确定没装」）
        rows["1_never_probed"] = await _row(client, devs["never"])

        # ② 探过 + 明确没装 bash → 缓存原文带 bash installed=false
        _mock_probe(monkeypatch, _probe(WIN_NO_BASH))
        r = await client.post(f"/api/devices/{devs['nobash']}/test")
        assert r.status_code == 200 and r.json()["ok"] is True, r.text
        rows["2_probed_no_bash"] = await _row(client, devs["nobash"])

        # ③ 探过 + 装了 bash → 绿的前提
        _mock_probe(monkeypatch, _probe(WIN_WITH_BASH))
        r = await client.post(f"/api/devices/{devs['withbash']}/test")
        assert r.status_code == 200 and r.json()["ok"] is True, r.text
        rows["3_probed_with_bash"] = await _row(client, devs["withbash"])

        # ④ 探过但没拿到任何运行时条目（remote_probe 的兜底形态）→ []，仍是「无结论」
        _mock_probe(monkeypatch, _probe([]))
        r = await client.post(f"/api/devices/{devs['empty']}/test")
        assert r.status_code == 200 and r.json()["ok"] is True, r.text
        rows["4_probed_empty"] = await _row(client, devs["empty"])

        # ── 断言 ───────────────────────────────────────────────────────────────
        assert rows["1_never_probed"]["runtimes"] is None
        assert rows["2_probed_no_bash"]["runtimes"] == WIN_NO_BASH
        assert rows["3_probed_with_bash"]["runtimes"] == WIN_WITH_BASH
        assert rows["4_probed_empty"]["runtimes"] == []

        # 关键回归点：「从未探测」≠「探过但没有 bash」，也不是「探过但没条目」
        assert rows["1_never_probed"]["runtimes"] != rows["2_probed_no_bash"]["runtimes"]
        assert rows["4_probed_empty"]["runtimes"] != rows["1_never_probed"]["runtimes"]
        assert rows["4_probed_empty"]["runtimes"] != rows["2_probed_no_bash"]["runtimes"]

        # 前端判据：.sh（ALT_RUNTIME_RULES: shell→bash）在远程 Windows 设备上的三态解析
        def bash_of(row):
            arr = row["runtimes"]
            if not arr:
                return None          # 无结论 → 前端保持中性（黄）
            hit = next((r for r in arr if r["name"] == "bash"), None)
            return None if hit is None else hit["installed"]

        assert bash_of(rows["1_never_probed"]) is None
        assert bash_of(rows["2_probed_no_bash"]) is False
        assert bash_of(rows["3_probed_with_bash"]) is True
        assert bash_of(rows["4_probed_empty"]) is None

        # 缓存不持久化：进程重启即失（SPEC §7.2-#2 的「最近一次探测」语义）→ 清空即回到未探测
        _cache().clear()
        assert (await _row(client, devs["withbash"]))["runtimes"] is None

        return rows

    rows = _run(scenario)
    print("\n── GET /api/devices 原始片段（证据）──")
    for k in sorted(rows):
        print(f"[{k}] {json.dumps(rows[k], ensure_ascii=False)}")
