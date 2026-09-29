"""脚本读取/保存的编码探测 + 原编码写回（防「静默乱码 → 写坏源文件」）。

旧行为：GET 读 UTF-8 失败 → latin-1（能解任意字节、永不报错）→ 界面显示乱码却"成功"；
        PUT 一律 write_text(..., "utf-8") → 用户一点保存，GBK 源文件永久损坏。
核心断言：**保存（内容未改）前后磁盘字节 sha256 完全一致**。

跑法：
    cd backend && .venv/bin/python -m pytest tests/test_script_text_encoding.py -q
隔离：DATA_DIR / 脚本根 / OS 配置目录全指向 tmp_path，不碰真库、不碰仓库 data/、不碰 ~/.config。
"""
import asyncio
import hashlib
import sys
from pathlib import Path

import httpx
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

GBK_BAT = "@echo off\r\n@REM 案例：清理脚本\r\necho 中文测试\r\nexit /b 0\r\n".encode("cp936")
UTF8_SH = "#!/bin/bash\n# 中文注释\necho 中文测试\n".encode("utf-8")
# 两种候选编码都解不出（UTF-8/cp936/gb18030 全失败：0xff/0xfe/0xfd 均非合法起始字节）
UNDECODABLE = b"echo \xff\xfe\xfd\r\n"
# 含 NUL → 二进制/UTF-16 类，不得拿去"成功"解码成乱码
NUL_BIN = b"echo \x81\x00\xff\r\n"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """隔离 DATA_DIR / 脚本根 / OS 配置目录，再导入 app（config.py 在 import 时解析路径）。"""
    data = tmp_path / "data"
    root = tmp_path / "scripts"
    monkeypatch.setenv("SCRIPTHUB_DATA_DIR", str(data))
    monkeypatch.setenv("SCRIPTHUB_SCRIPTS_ROOT", str(root))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfghome"))
    monkeypatch.setenv("SCRIPTHUB_WIN_ANSI_CODEC", "cp936")   # 固定目标 ANSI，断言可复现
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]
    data.mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    yield {"root": root, "data": data}
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]


async def _client(isolated):
    """建库 → 落 4 个不同编码的脚本文件 → 真实扫描入库 → 返回 (client, {name: id})"""
    import app.models  # noqa: F401 — 注册全部 ORM 模型
    from app.database import init_db
    from app.main import app

    await init_db()
    (isolated["root"] / "gbk_case.bat").write_bytes(GBK_BAT)
    (isolated["root"] / "utf8_case.sh").write_bytes(UTF8_SH)
    (isolated["root"] / "bad_case.bat").write_bytes(UNDECODABLE)
    (isolated["root"] / "nul_case.bat").write_bytes(NUL_BIN)

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    scan = await client.post("/api/scripts/scan")
    assert scan.status_code == 200, scan.text
    items = (await client.get("/api/scripts")).json()["items"]
    by_name = {i["name"]: i["id"] for i in items}
    for n in ("gbk_case.bat", "utf8_case.sh", "bad_case.bat", "nul_case.bat"):
        assert n in by_name, f"{n} 未入库（扫描只认扩展名，不该受编码影响）: {sorted(by_name)}"
    return client, by_name


def _run(isolated, scenario):
    async def body():
        client, by_name = await _client(isolated)
        try:
            return await scenario(client, by_name, isolated)
        finally:
            await client.aclose()

    return asyncio.run(body())


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_gbk_read_and_save_roundtrip_bytes_identical(isolated):
    """① GBK .bat 读出来是正确中文 + 报命中编码 ② 原样保存 → 磁盘字节 sha256 不变"""
    async def scenario(client, by_name, iso):
        p = iso["root"] / "gbk_case.bat"
        before = _sha(p)

        r = await client.get(f"/api/scripts/{by_name['gbk_case.bat']}/content")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "\ufffd" not in body["content"], f"读出了替换字符（就是本次要堵的乱码）: {body['content']!r}"
        assert "中文测试" in body["content"] and "案例：清理脚本" in body["content"], body["content"]
        assert body["encoding"] == "cp936", f"未报告命中编码: {body}"
        assert "encoding_warning" in body and "GBK" in body["encoding_warning"], body

        # 原样保存（不带 encoding：服务端按磁盘现状判定 → 必须写回 cp936）
        r2 = await client.put(f"/api/scripts/{by_name['gbk_case.bat']}/content",
                              json={"content": body["content"]})
        assert r2.status_code == 200, r2.text
        assert r2.json()["encoding"] == "cp936", r2.json()
        after = _sha(p)
        assert after == before, f"保存后字节变了（数据损坏）：{before} -> {after}"
        assert p.read_bytes() == GBK_BAT, "内容与原始 GBK 字节不一致"

    assert _run(isolated, scenario) is None


def test_gbk_edit_keeps_gbk_encoding(isolated):
    """③ 编辑后保存：仍按 GBK 写回（其余中文不损坏），不因编辑被悄悄转成 UTF-8"""
    async def scenario(client, by_name, iso):
        p = iso["root"] / "gbk_case.bat"
        sid = by_name["gbk_case.bat"]
        text = (await client.get(f"/api/scripts/{sid}/content")).json()["content"]
        edited = text.replace("exit /b 0", "echo 编辑后的中文\r\nexit /b 0")
        r = await client.put(f"/api/scripts/{sid}/content", json={"content": edited})
        assert r.status_code == 200, r.text
        raw = p.read_bytes()
        assert raw != GBK_BAT, "编辑未落盘"
        assert "编辑后的中文" in raw.decode("cp936"), "新内容不是 GBK 写回"
        assert "案例：清理脚本" in raw.decode("cp936"), "原有中文被写坏"
        assert raw.decode("cp936").replace("\r\n", "\n") == edited.replace("\r\n", "\n")

    _run(isolated, scenario)


def test_unrepresentable_char_rejected_not_silently_replaced(isolated):
    """④ 编辑引入 GBK 表达不了的字符（emoji）→ 400 明确报错，磁盘字节不动（不许静默变 ?）"""
    async def scenario(client, by_name, iso):
        p = iso["root"] / "gbk_case.bat"
        sid = by_name["gbk_case.bat"]
        before = _sha(p)
        text = (await client.get(f"/api/scripts/{sid}/content")).json()["content"]

        r = await client.put(f"/api/scripts/{sid}/content",
                             json={"content": text + "echo 😀 表情\r\n"})
        assert r.status_code == 400, f"应 400 拒绝，实际 {r.status_code}: {r.text}"
        assert "无法表示" in r.json()["detail"] and "utf-8" in r.json()["detail"], r.json()
        assert _sha(p) == before, "被拒绝的保存却动了文件"
        assert "?" not in p.read_bytes().decode("cp936"), "静默替换成了 ?"

        # 显式覆盖入口：确实想转 UTF-8 时可用（并如实报告编码已变更）
        r2 = await client.put(f"/api/scripts/{sid}/content",
                              json={"content": text + "echo 😀 表情\r\n", "encoding": "utf-8"})
        assert r2.status_code == 200, r2.text
        assert r2.json()["encoding"] == "utf-8" and "编码已变更" in r2.json()["encoding_warning"], r2.json()
        assert p.read_bytes().decode("utf-8").endswith("echo 😀 表情\r\n"), "显式转码未生效"

    _run(isolated, scenario)


def test_utf8_sh_roundtrip_unchanged(isolated):
    """⑤ UTF-8 .sh：报告 utf-8、无告警；原样保存 → 字节不变（兜底不许误伤正常文件）"""
    async def scenario(client, by_name, iso):
        p = iso["root"] / "utf8_case.sh"
        before = _sha(p)
        sid = by_name["utf8_case.sh"]
        body = (await client.get(f"/api/scripts/{sid}/content")).json()
        assert body["encoding"] == "utf-8" and "encoding_warning" not in body, body
        assert body["content"].startswith("#!/bin/bash") and "中文注释" in body["content"], body

        r = await client.put(f"/api/scripts/{sid}/content", json={"content": body["content"]})
        assert r.status_code == 200 and _sha(p) == before, "UTF-8 文件往返被改动"
        assert p.read_bytes() == UTF8_SH

    _run(isolated, scenario)


def test_undecodable_bytes_error_not_mojibake(isolated):
    """⑥ 两种编码都解不出 → 400 明确报错（不许 latin-1 兜出乱码）；显式指定编码才放行"""
    async def scenario(client, by_name, iso):
        p = iso["root"] / "bad_case.bat"
        before = _sha(p)
        sid = by_name["bad_case.bat"]

        r = await client.get(f"/api/scripts/{sid}/content")
        assert r.status_code == 400, f"应 400，实际 {r.status_code}: {r.text}"
        assert "无法识别文件编码" in r.json()["detail"], r.json()

        # 不给编码 → 服务端也定不了原编码 → 拒绝写（不许默认 UTF-8 覆盖）
        r2 = await client.put(f"/api/scripts/{sid}/content", json={"content": "echo x\r\n"})
        assert r2.status_code == 400 and "无法确定原文件编码" in r2.json()["detail"], r2.text
        assert _sha(p) == before, "被拒绝的保存动了文件"

        # 逃生口：显式指定编码（latin-1 逐字节可逆）→ 可写，且原样往返字节不变
        r3 = await client.put(f"/api/scripts/{sid}/content",
                              json={"content": UNDECODABLE.decode("latin-1"), "encoding": "latin-1"})
        assert r3.status_code == 200, r3.text
        assert _sha(p) == before, "latin-1 逐字节往返应当不变"

        # 非法编码名 → 400（不是 500）
        r4 = await client.put(f"/api/scripts/{sid}/content", json={"content": "x", "encoding": "no_such_codec"})
        assert r4.status_code == 400 and "未知编码" in r4.json()["detail"], r4.text

    _run(isolated, scenario)


def test_nul_bytes_rejected(isolated):
    """⑦ 含 NUL（二进制/UTF-16 类）→ 400 报"疑似二进制"，不产出乱码"""
    async def scenario(client, by_name, iso):
        r = await client.get(f"/api/scripts/{by_name['nul_case.bat']}/content")
        assert r.status_code == 400 and "二进制" in r.json()["detail"], r.text

    _run(isolated, scenario)


def test_ansi_knob_and_utf8_priority(isolated):
    """⑧ 复用 X 的旋钮：目标 ANSI 非 cp936（cp950）时仍先试 UTF-8，UTF-8 文件不受影响。

    旋钮在 _encoding_candidates 里每次读 env，故这里直接调纯函数（不必经接口）。"""
    import os
    from app.routers.script import probe_script_text
    tw = "繁體測試=案例".encode("cp950")
    os.environ["SCRIPTHUB_WIN_ANSI_CODEC"] = "cp950"
    try:
        text, enc = probe_script_text(tw)
        assert enc == "cp950" and text == "繁體測試=案例", (enc, text)
        # UTF-8 优先：UTF-8 内容不许因为旋钮指向 cp950 而被 GBK 抢先解出乱码
        text, enc = probe_script_text(GBK_BAT.decode("cp936").encode("utf-8"))
        assert enc == "utf-8" and "中文测试" in text, (enc, text)
        # 带 BOM：按 utf-8-sig 读（BOM 不进文本），保存时同一编码写回仍带 BOM
        text, enc = probe_script_text(b"\xef\xbb\xbf" + "@echo off\r\n".encode())
        assert enc == "utf-8-sig" and text == "@echo off\r\n" and not text.startswith("\ufeff"), (enc, text)
    finally:
        os.environ["SCRIPTHUB_WIN_ANSI_CODEC"] = "cp936"


def test_bom_file_roundtrip_bytes_identical(isolated):
    """⑨ 带 UTF-8 BOM 的文件：读取不把 BOM 当正文、保存写回仍带 BOM（字节不变）"""
    async def scenario(client, by_name, iso):
        p = iso["root"] / "bom_case.ps1"
        p.write_bytes(b"\xef\xbb\xbf" + "Write-Output \"中文\"\r\n".encode("utf-8"))
        assert (await client.post("/api/scripts/scan")).status_code == 200
        items = (await client.get("/api/scripts")).json()["items"]
        sid = next(i["id"] for i in items if i["name"] == "bom_case.ps1")
        before = _sha(p)

        body = (await client.get(f"/api/scripts/{sid}/content")).json()
        assert body["encoding"] == "utf-8-sig" and body["content"] == "Write-Output \"中文\"\r\n", body
        r = await client.put(f"/api/scripts/{sid}/content", json={"content": body["content"]})
        assert r.status_code == 200, r.text
        assert _sha(p) == before, "BOM 文件往返字节变了"
        assert p.read_bytes().startswith(b"\xef\xbb\xbf"), "BOM 丢了"

    _run(isolated, scenario)


# ── 批次 AJ：保存时的行尾保全 ─────────────────────────────────────────────────
# 浏览器 <textarea> 按 HTML 规范把内容里的 \r\n 规范化成 \n，所以前端送到 PUT 的内容**已经
# 是 LF 化的**（真机：CRLF 的 cleanup.bat 编辑一次后磁盘变成纯 LF，108 → 107 字节）。
# PUT 按磁盘原文件行尾还原；磁盘是 LF 则原样写 LF。

CRLF_BAT = b"@echo off\r\necho Done!\r\n"


async def _seed(client, iso, name: str, raw: bytes) -> int:
    """把 raw 落到脚本根下并重扫，返回它的 script id。"""
    (iso["root"] / name).write_bytes(raw)
    assert (await client.post("/api/scripts/scan")).status_code == 200
    items = (await client.get("/api/scripts")).json()["items"]
    return next(i["id"] for i in items if i["name"] == name)


def test_crlf_file_keeps_crlf_after_edit(isolated):
    """⑩ CRLF 文件：编辑保存后磁盘仍是 CRLF（textarea 送来的是 LF，服务端还原）"""
    async def scenario(client, by_name, iso):
        p = iso["root"] / "crlf_case.bat"
        sid = await _seed(client, iso, "crlf_case.bat", CRLF_BAT)

        text = (await client.get(f"/api/scripts/{sid}/content")).json()["content"]
        assert "\r\n" in text, f"读取未原样返回 CRLF（前端行尾标记就靠它）: {text!r}"
        # 用户在编辑框里改一个字；textarea 交出来的内容全是 LF（这就是前端实际 PUT 的东西）
        edited = text.replace("\r\n", "\n").replace("Done!", "好耶!")
        assert "\r" not in edited

        r = await client.put(f"/api/scripts/{sid}/content", json={"content": edited})
        assert r.status_code == 200, r.text
        raw = p.read_bytes()
        assert raw == "@echo off\r\necho 好耶!\r\n".encode(), raw
        assert raw.count(b"\n") == raw.count(b"\r\n"), f"写回后仍有裸 LF: {raw!r}"
        assert b"\r\r\n" not in raw, "已有 CRLF 被重复转换（\\r\\r\\n）"

    _run(isolated, scenario)


def test_lf_file_stays_lf_after_edit(isolated):
    """⑪ 反方向：LF 文件编辑保存后仍是 LF（不许被无条件转成 CRLF）"""
    async def scenario(client, by_name, iso):
        p = iso["root"] / "lf_case.sh"
        sid = await _seed(client, iso, "lf_case.sh", b"#!/bin/bash\necho hello\n")

        text = (await client.get(f"/api/scripts/{sid}/content")).json()["content"]
        r = await client.put(f"/api/scripts/{sid}/content",
                             json={"content": text.replace("hello", "hi")})
        assert r.status_code == 200, r.text
        raw = p.read_bytes()
        assert raw == b"#!/bin/bash\necho hi\n", raw
        assert b"\r" not in raw, f"LF 文件被写成了 CRLF: {raw!r}"

    _run(isolated, scenario)


def test_noop_save_does_not_touch_disk(isolated):
    """⑫ 内容未改的保存：不写盘（字节 + mtime 都不动）——行尾还原后与原文逐字节相同，
    仍走既有「字节相同则跳过写盘」那条路，不靠巧合。"""
    async def scenario(client, by_name, iso):
        p = iso["root"] / "crlf_case.bat"
        sid = await _seed(client, iso, "crlf_case.bat", CRLF_BAT)
        before_sha, before_mtime = _sha(p), p.stat().st_mtime_ns

        text = (await client.get(f"/api/scripts/{sid}/content")).json()["content"]
        r = await client.put(f"/api/scripts/{sid}/content",
                             json={"content": text.replace("\r\n", "\n")})
        assert r.status_code == 200, r.text
        assert p.read_bytes() == CRLF_BAT, "内容未改却改动了文件"
        assert _sha(p) == before_sha
        assert p.stat().st_mtime_ns == before_mtime, \
            f"内容未改却重写了文件（mtime 变了）: {before_mtime} -> {p.stat().st_mtime_ns}"

    _run(isolated, scenario)
