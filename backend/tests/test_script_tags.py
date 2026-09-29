"""脚本标签关联回归（D1）。

根因：`PUT /api/scripts/{id}/tags` 在替换关联时用 ORM `db.delete()` 标记删除，
flush 时 INSERT 先于 DELETE 发出，同一 (script_id, tag_id) 上撞 uq_script_tag →
脚本已有标签时再保存（集合含任一旧标签）必 500，标签功能实际不可用。

跑法：cd backend && .venv/bin/python -m pytest tests/test_script_tags.py -q
隔离：DATA_DIR / 脚本根 / OS 配置目录全部指向 tmp_path，不碰真库、不碰 ~/.config。
"""
import asyncio
import sqlite3
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


async def _app_client(isolated):
    """建库 → 落一个脚本文件走真实扫描入库 → 建标签 A/B/C。返回 (client, script_id, [id...])。"""
    import app.models  # noqa: F401 — 注册全部 ORM 模型，create_all 才有表可建
    from app.database import init_db
    from app.main import app

    await init_db()
    (isolated["root"] / "demo.py").write_text("print('demo')\n")

    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )
    scan = await client.post("/api/scripts/scan")
    assert scan.status_code == 200, scan.text
    sid = (await client.get("/api/scripts")).json()["items"][0]["id"]

    tag_ids = []
    for name in ("A", "B", "C"):
        r = await client.post("/api/tags", json={"name": name})
        assert r.status_code == 200, r.text
        tag_ids.append(r.json()["id"])
    return client, sid, tag_ids


def _run(isolated, scenario) -> int:
    """一个事件循环内跑完整场景（建库、建客户端、断言、关客户端），返回 script_id。"""
    async def body():
        client, sid, tag_ids = await _app_client(isolated)
        try:
            await scenario(client, sid, tag_ids)
        finally:
            await client.aclose()
        return sid

    return asyncio.run(body())


def _links(isolated, sid: int) -> int:
    """直接读隔离库数关联行（真实落盘状态，不走 ORM 缓存）。"""
    con = sqlite3.connect(isolated["data"] / "scripthub.db")
    try:
        return con.execute(
            "SELECT COUNT(*) FROM script_tags WHERE script_id = ?", (sid,)
        ).fetchone()[0]
    finally:
        con.close()


def test_first_set_ok(isolated):
    """① 空 → 打 [A,B]：200，关联 = 2。"""
    async def scenario(client, sid, tag_ids):
        a, b, _c = tag_ids
        r = await client.put(f"/api/scripts/{sid}/tags", json={"tag_ids": [a, b]})
        assert r.status_code == 200, r.text
        assert r.json()["tags"] == ["A", "B"]
        assert (await client.get(f"/api/scripts/{sid}")).json()["tags"] == ["A", "B"]

    assert _links(isolated, _run(isolated, scenario)) == 2


def test_resave_with_existing_tags_ok(isolated):
    """② 已有 [A,B] → 再打 [A,B,C]：必须 200（本 bug 回归点），关联 = 3。"""
    async def scenario(client, sid, tag_ids):
        a, b, c = tag_ids
        first = await client.put(f"/api/scripts/{sid}/tags", json={"tag_ids": [a, b]})
        assert first.status_code == 200, first.text

        second = await client.put(f"/api/scripts/{sid}/tags", json={"tag_ids": [a, b, c]})
        assert second.status_code == 200, f"再保存含旧标签的集合不得 500：{second.text}"
        assert second.json()["tags"] == ["A", "B", "C"]

        # 幂等：同集合再打一次仍 200，且不产生重复关联行
        third = await client.put(f"/api/scripts/{sid}/tags", json={"tag_ids": [a, b, c]})
        assert third.status_code == 200, third.text
        assert (await client.get(f"/api/scripts/{sid}")).json()["tags"] == ["A", "B", "C"]

    sid = _run(isolated, scenario)
    assert _links(isolated, sid) == 3  # 重复保存不得留下重复行


def test_unknown_tag_id_4xx(isolated):
    """③ 打不存在的 [999]：4xx（不是 500），且既有关系不被破坏。"""
    async def scenario(client, sid, tag_ids):
        a, _b, _c = tag_ids
        ok = await client.put(f"/api/scripts/{sid}/tags", json={"tag_ids": [a]})
        assert ok.status_code == 200, ok.text

        r = await client.put(f"/api/scripts/{sid}/tags", json={"tag_ids": [999]})
        assert r.status_code != 500, f"非法 tag_id 不得 500：{r.text}"
        assert 400 <= r.status_code < 500, f"期望 4xx，得到 {r.status_code}: {r.text}"
        assert r.status_code == 404 and "Tag not found" in r.json()["detail"]

        # 部分非法同样拒绝：不得静默只写合法的那部分
        r2 = await client.put(f"/api/scripts/{sid}/tags", json={"tag_ids": [a, 999]})
        assert r2.status_code == 404, r2.text

        assert (await client.get(f"/api/scripts/{sid}")).json()["tags"] == ["A"]

    assert _links(isolated, _run(isolated, scenario)) == 1
