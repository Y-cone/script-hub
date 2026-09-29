"""脚本写盘的原子性（④）——写入中途失败不得破坏原文件、不得留临时文件。

改前：所有写盘都是 `path.write_bytes(data)`（整文件覆盖）——写到一半崩溃/断电就把原文件截断，
      原内容永久丢失（上传覆盖同名脚本、编辑保存都走这条路径）。
改后：唯一写盘入口 routers/script.py:atomic_write_bytes = 同目录 mkstemp → write → fsync →
      os.replace（原子提交）；异常路径 finally 清理临时文件。

跑法：cd backend && .venv/bin/python -m pytest tests/test_atomic_write.py -q
"""
import os
import stat
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.routers.script import atomic_write_bytes   # noqa: E402

ORIGINAL = "echo 原始内容\r\n".encode("cp936")


def test_write_failure_keeps_original_and_leaves_no_tmp_file(tmp_path, monkeypatch):
    """fsync 中途炸（磁盘满/IO 错）→ 原文件字节不变，目录里不留 .tmp 残渣。"""
    target = tmp_path / "cleanup.bat"
    target.write_bytes(ORIGINAL)
    inode_before = target.stat().st_ino

    def boom(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fsync", boom)

    with pytest.raises(OSError):
        atomic_write_bytes(target, "echo 新内容\r\n".encode("cp936"))

    assert target.read_bytes() == ORIGINAL, "失败的写入破坏了原文件"
    assert target.stat().st_ino == inode_before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cleanup.bat"], "留下了临时文件"


def test_normal_write_replaces_content_atomically(tmp_path):
    """正常路径：内容完整落盘、权限位保留（覆盖 0600 不能吃掉可执行位）、无临时文件残留。"""
    target = tmp_path / "run.sh"
    target.write_bytes(ORIGINAL)
    os.chmod(target, 0o755)

    atomic_write_bytes(target, b"#!/bin/bash\necho ok\n")

    assert target.read_bytes() == b"#!/bin/bash\necho ok\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o755, "覆盖写盘丢掉了原权限位"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["run.sh"]


def test_write_to_new_path_creates_file(tmp_path):
    """目标不存在（首次上传）也要能落盘。"""
    target = tmp_path / "new.bat"
    atomic_write_bytes(target, b"@echo off\r\n")
    assert target.read_bytes() == b"@echo off\r\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["new.bat"]
