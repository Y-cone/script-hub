"""批次 AM②：`detect_has_bash` 改成**读穿透** probe 缓存 —— 离线断言，不联网、不建真库。

背景（AL 发现的两处硬伤，用户已确认要修）：
  ① 一次网络抖动/超时被当成「没装 bash」，而且**永久**缓存（`_BASH_CACHE`，无 TTL）
     → 那台机器的 .sh 到重启前一直被拒；
  ② 永久缓存 → 新装 Git Bash 不生效（用户正是踩在这上面）。

本文件锁死读穿透的四件事：
  · 缓存新鲜 + 有 bash 条目 → 直接用，**一次网络都不发**；
  · 未命中 / 过期 → 现探一次，并把结论写回**同一份** probe 缓存（列表接口/脚本库三态跟着变新）；
  · 探测本身失败（异常 / 超时收敛成空输出）→ **不写缓存**，返回 True —— 抖动不再变成「没装」；
  · 写回是**合并**：同一次 remote_probe 的 python/powershell 条目不能被 bash 一条挤掉。

另含契约回归：`GET /api/devices`（列表）仍**不主动探测**（SPEC §7.2-#2 ①），
但读穿透写进去的结论它立刻就看得见。

跑法：cd backend && .venv/bin/python -m pytest tests/test_bash_readthrough_cache.py -q -s
隔离：DATA_DIR / 脚本根 / OS 配置目录全指 tmp_path；exec_command 被替换；连接池上挂兜网
      （见 _no_net），任何真去连设备的路径立刻炸在测试里。
"""
import asyncio
import importlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

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


def _ssh():
    """现取活模块：isolated fixture 会 `del sys.modules['app.*']`，模块顶部 `import … as x`
    拿到的对象随后就成了死对象，补丁/断言都会打在死对象上。"""
    return importlib.import_module("app.services.ssh_service")


def _cache() -> dict:
    return _ssh()._probe_cache


def _detect(device_id: int) -> bool:
    """只用到 device.id → 用 SimpleNamespace 造个壳就够，不必建真 Device 行。"""
    return asyncio.run(_ssh().detect_has_bash(SimpleNamespace(id=device_id)))


def _patch_exec(monkeypatch, result=None, exc=None) -> list:
    """替换 exec_command 并记录每次调用（打**模块属性**：detect_has_bash 运行时现取这个名字）。
    返回调用记录 —— 断言「没多发一次」就靠它。"""
    calls: list = []

    async def fake(device, command, timeout=120, output_cb=None, cancel_event=None):
        calls.append(command)
        if exc is not None:
            raise exc
        return result

    monkeypatch.setattr(_ssh(), "exec_command", fake)
    return calls


def _no_net(monkeypatch):
    """兜网：没盖住的路一旦去取连接池 → 立刻炸在测试里，而不是真去连设备。"""
    ssh = _ssh()

    async def boom(device):
        raise AssertionError(f"测试试图建立 SSH 连接（host={device.host!r}）——mock 没盖住")

    monkeypatch.setattr(type(ssh.pool), "get", boom)   # 打类，不打实例


def _seed(device_id: int, runtimes, age: float = 0.0) -> dict:
    """往缓存里塞一条（age 秒前写入的）结论；age > CACHE_TTL 即「已过期」。"""
    ent = {"ts": time.monotonic() - age, "ok": True, "os_info": "Windows_NT",
           "latency_ms": 12, "error": None, "runtimes": runtimes}
    _cache()[device_id] = ent
    return ent


BASH_OK_OUT = "GNU bash, version 5.2.26(1)-release (x86_64-pc-msys)"
BASH_NOT_FOUND_OUT = "'bash' 不是内部或外部命令，也不是可运行的程序\n或批处理文件。"
PY_ITEM = {"name": "python", "installed": True, "version": "Python 3.12.3"}
PS_ITEM = {"name": "powershell", "installed": True, "version": "5.1.22621"}
BASH_ITEM = {"name": "bash", "installed": True, "version": "5.2.26(1)-release"}


def _bash_installed(device_id: int):
    """从缓存里读 bash 三态（前端 / 列表接口的同一判据）：True/False/None（无结论）。"""
    ent = _cache().get(device_id)
    arr = (ent or {}).get("runtimes")
    if not arr:
        return None
    hit = next((r for r in arr if r["name"] == "bash"), None)
    return None if hit is None else hit["installed"]


def _ttl() -> float:
    return _ssh().CACHE_TTL


# ── ① 缓存新鲜 → 直接用，零额外开销 ────────────────────────────────────────────────

def test_fresh_cache_hit_does_not_probe(isolated, monkeypatch):
    _no_net(monkeypatch)
    calls = _patch_exec(monkeypatch, (0, "SHOULD-NOT-BE-CALLED"))
    _seed(9001, [PY_ITEM, BASH_ITEM])

    assert _detect(9001) is True
    assert calls == [], f"命中缓存还发了 {len(calls)} 次探测：{calls}"
    # 结论来自缓存条目本身（不是现探的）→ 版本号仍是缓存里的
    assert _bash_installed(9001) is True


# ── ② 未命中 → 探一次 + 写回同一份缓存（新装 Git Bash 立刻生效）─────────────────────

def test_cache_miss_probes_once_and_writes_back(isolated, monkeypatch):
    _no_net(monkeypatch)
    calls = _patch_exec(monkeypatch, (0, BASH_OK_OUT))
    assert not hasattr(_ssh(), "_BASH_CACHE"), "旧的无 TTL 永久缓存不该再回来"

    assert _detect(9002) is True                    # 新装 Git Bash：缓存里什么都没有也能探到
    assert len(calls) == 1, calls
    assert _bash_installed(9002) is True            # ★ 结论进了**同一份** probe 缓存
    assert _cache()[9002]["runtimes"] == [
        {"name": "bash", "installed": True, "version": BASH_OK_OUT}]

    assert _detect(9002) is True                    # 第二次：缓存已新鲜 → 不再探
    assert len(calls) == 1, f"写回后仍重复探测：{calls}"


def test_expired_cache_reprobes_and_drops_stale_entries(isolated, monkeypatch):
    """过期缓存 = 无结论（旧结论不续命）；重探后以新结论为准 —— 卸载/安装都靠这条生效。"""
    _no_net(monkeypatch)
    calls = _patch_exec(monkeypatch, (0, BASH_OK_OUT))
    _seed(9003, [PY_ITEM, {"name": "bash", "installed": False, "version": "探测失败: old"}],
          age=_ttl() + 100)

    assert _detect(9003) is True                    # ★ 旧结论说「没装」，实测装了 → 立刻翻绿
    assert len(calls) == 1, calls
    names = [r["name"] for r in _cache()[9003]["runtimes"]]
    assert names == ["bash"], f"过期的兄弟条目不该被续命：{names}"


def test_write_back_merges_sibling_runtimes(isolated, monkeypatch):
    """写回是合并：同一次 remote_probe 的 python/powershell 还在数组里，不能被 bash 顶掉。"""
    _no_net(monkeypatch)
    _patch_exec(monkeypatch, (0, BASH_OK_OUT))
    _seed(9004, [PY_ITEM, PS_ITEM])                 # 新鲜缓存：探过，但没有 bash 条目

    assert _detect(9004) is True
    assert _cache()[9004]["runtimes"] == [PY_ITEM, PS_ITEM, {**BASH_ITEM, "version": BASH_OK_OUT}]


# ── ③ 探测失败 → 不写缓存 + 返回 True（抖动不许变成「没装」）─────────────────────────

def test_probe_exception_is_not_cached_and_returns_true(isolated, monkeypatch):
    _no_net(monkeypatch)
    calls = _patch_exec(monkeypatch, exc=TimeoutError("Connection timed out"))
    before = _seed(9005, [PY_ITEM])                 # 新鲜缓存，但里面没有 bash 结论

    assert _detect(9005) is True                    # ★ 不稳定 → 放行，不误拒
    assert len(calls) == 1, calls
    assert _cache()[9005] is before, "探测失败却动了缓存"
    assert _cache()[9005]["ts"] == before["ts"]     # ts 也没被刷新（下次会再探）
    assert _bash_installed(9005) is None            # 抖动没有变成「确定没装 bash」


def test_empty_output_is_no_conclusion(isolated, monkeypatch):
    """exec_command 内部超时/半关闭会收敛成 code=-1 + 空输出 —— 那也是「没探到」，不是「没装」。"""
    _no_net(monkeypatch)
    calls = _patch_exec(monkeypatch, (-1, ""))
    before = _seed(9006, [PY_ITEM])

    assert _detect(9006) is True
    assert len(calls) == 1, calls
    assert _cache()[9006] is before
    assert _bash_installed(9006) is None


def test_real_not_installed_is_false_and_cached(isolated, monkeypatch):
    """真没装（cmd 报 not recognized）→ 红是真红，且结论要落缓存给脚本库用。"""
    _no_net(monkeypatch)
    calls = _patch_exec(monkeypatch, (1, BASH_NOT_FOUND_OUT))
    assert _detect(9007) is False
    assert len(calls) == 1, calls
    assert _bash_installed(9007) is False


# ── ④ 契约 + 端到端：列表接口不探测，但看得见读穿透的结论 ─────────────────────────────

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


def test_list_endpoint_never_probes_but_sees_readthrough(isolated, monkeypatch):
    """SPEC §7.2-#2 ①：`GET /api/devices` 不得主动探测（_no_net 挂着，一探就炸）；
    读穿透写进缓存的结论它立刻看得到 → 脚本库三态无需等 TTL 到期或重启。"""
    _no_net(monkeypatch)

    async def scenario(client):
        rows: dict = {}
        dev = await _mk_device(client, "w1", "10.0.2.11")

        # ① 从未探测：列表照常返回（没触发任何 SSH），runtimes 仍是「无结论」
        rows["1_never_probed"] = await _row(client, dev)
        assert rows["1_never_probed"]["runtimes"] is None

        # ② 执行路径上的读穿透探到 bash → 同一份缓存被写 → 列表立刻给出结论
        calls = _patch_exec(monkeypatch, (0, BASH_OK_OUT))
        assert await _ssh().detect_has_bash(SimpleNamespace(id=dev)) is True
        assert len(calls) == 1
        rows["2_after_readthrough"] = await _row(client, dev)
        assert rows["2_after_readthrough"]["runtimes"] == [
            {"name": "bash", "installed": True, "version": BASH_OK_OUT}]
        assert rows["2_after_readthrough"]["online"] is True     # 探通了 = 设备可达

        # ③ 读穿透失败（抖动）→ 列表结论不被污染（不会因为探不到就说没装）
        _patch_exec(monkeypatch, exc=TimeoutError("timed out"))
        assert await _ssh().detect_has_bash(SimpleNamespace(id=dev)) is True
        rows["3_after_jitter"] = await _row(client, dev)
        assert rows["3_after_jitter"]["runtimes"] == rows["2_after_readthrough"]["runtimes"]
        return rows

    rows = _run(scenario)
    print("\n── GET /api/devices 原始片段（证据）──")
    for k in sorted(rows):
        print(f"[{k}] {json.dumps(rows[k], ensure_ascii=False)}")
