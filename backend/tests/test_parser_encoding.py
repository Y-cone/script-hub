"""parser 读源码的编码（⑤）——GBK 的 .bat 参数列表要读得出来，探测不出要**明确失败**。

改前：`_read_source` 一律 `filepath.read_text(encoding="utf-8")` —— Windows 目标上按系统 ANSI
      （cp936/GBK）存的 .bat/.ps1/.sh 直接 UnicodeDecodeError，参数列表解析不出来（POST /{id}/parse
      500）。少数路径还退化成静默 `return []`。
改后：`_read_source` 复用批次 AA 的 script_text.probe_script_text（唯一探测实现），探测不出 →
      ValueError 上抛（不引入 latin-1 之类"永不失败"的兜底——那正是写坏源文件的起点）。

跑法：cd backend && .venv/bin/python -m pytest tests/test_parser_encoding.py -q
"""
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services.parser import parse_script, _read_source   # noqa: E402

GBK_BAT = ("@echo off\r\nREM 案例：清理临时目录\r\necho 开始清理\r\n"
           'echo %1 %2\r\nif "%1"=="" echo 用法：cleanup.bat 目录 天数\r\nexit /b 0\r\n')


def test_gbk_bat_parses(tmp_path):
    """GBK 中文 .bat：能读出源码与位置参数（改前 UnicodeDecodeError）。"""
    p = tmp_path / "cleanup.bat"
    p.write_bytes(GBK_BAT.encode("gbk"))

    with pytest.raises(UnicodeDecodeError):      # 改前的读法（固定 utf-8）确实解不出
        p.read_bytes().decode("utf-8")

    source = _read_source(p)
    assert "案例：清理临时目录" in source, "GBK 内容没解对"

    params = parse_script(p, "bat")
    assert [x["name"] for x in params] == ["%1", "%2"], params


def test_utf8_still_preferred_and_unchanged(tmp_path):
    """UTF-8 文件（默认情况）行为不变：优先 UTF-8，不因新增探测而被 GBK 抢走。"""
    src = "@echo off\necho 中文注释\necho %1\n"
    p = tmp_path / "u.bat"
    p.write_bytes(src.encode("utf-8"))
    assert _read_source(p) == src
    assert [x["name"] for x in parse_script(p, "bat")] == ["%1"]


def test_undecodable_source_fails_loudly(tmp_path):
    """三种候选编码（utf-8 / 目标 ANSI(cp936) / gb18030）都解不出 → ValueError，
    绝不静默返回 []（"解析不出参数"和"读不了这个文件"必须能区分）。"""
    p = tmp_path / "broken.bat"
    p.write_bytes(b"\xff\xff")          # 在 utf-8 / cp936 / gb18030 下都是非法序列

    with pytest.raises(ValueError) as ei:
        _read_source(p)
    assert "编码" in str(ei.value)
    with pytest.raises(ValueError):
        parse_script(p, "bat")


def test_binary_source_rejected(tmp_path):
    """含 NUL（二进制/UTF-16）→ 明确拒绝，不是猜一个编码读成乱码。"""
    p = tmp_path / "bin.bat"
    p.write_bytes(b"MZ\x00\x00\x90\x00")
    with pytest.raises(ValueError) as ei:
        parse_script(p, "bat")
    assert "二进制" in str(ei.value)
