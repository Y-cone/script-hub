"""Unix 目标 .sh 上传行尾规整自检（批次 AH）。

跑法：cd backend && .venv/bin/python -m pytest tests/test_unix_sh_line_endings.py -q
（写成 test_* 函数式：`pytest tests` 会收集本文件。仓库里 main() 式的
  test_win_script_encoding.py / test_shell_runner.py / test_terminal_shells.py
  已于批次 AJ 接回收集，见各文件末尾的 test_main_selfcheck()——
  断言必须落在被收集的文件里才真的每次都被跑。）

覆盖：
 1) Unix + .sh + CRLF → LF，且**除行尾外逐字节不变**（含 UTF-8 中文非 ASCII 字节）
 2) 已是 LF → 幂等原样返回
 3) 裸 `\\r`（脚本自己的控制字符）不许被误删
 4) 无后缀但带 shebang 的随传依赖 → 同样规整；无 shebang 的无后缀文件原样
 5) 不该动的后缀（.py/.json/二进制/.ps1/…）在 Unix 目标下一律原样——依赖清单里可能有二进制
 6) Windows 方向既有行为不受影响（.bat→cp936+CRLF、.ps1→UTF-8 BOM）
 7) put_script_encoded 走的整条上传链（executor:590 / sched_delegate:106）落的盘字节正确
"""
import codecs
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.executor import (  # noqa: E402
    SHELL_SUFFIXES, _to_lf, encode_script_bytes, put_script_encoded)

CRLF_SH = b"#!/bin/bash\r\necho hello\r\n"
LF_SH = b"#!/bin/bash\necho hello\n"
ZH_CRLF = "#!/bin/bash\r\necho 中文测试\r\n".encode("utf-8")
BIN_DEP = b"PK\x03\x04\r\n\x00binary\r\n"


def test_unix_sh_crlf_to_lf_is_line_ending_only():
    payload, warn = encode_script_bytes(CRLF_SH, False, "x.sh")
    assert warn is None
    assert b"\r\n" not in payload
    assert len(payload) == len(CRLF_SH) - 2      # 两个行尾各掉一个 \r，没别的增删
    # 非循环断言：逐行按原字节比（不是拿 _to_lf 的结果去比 _to_lf）
    for got, src in zip(payload.split(b"\n"), CRLF_SH.split(b"\r\n")):
        assert got == src, (got, src)


def test_unix_sh_utf8_encoding_untouched():
    payload, warn = encode_script_bytes(ZH_CRLF, False, "中文.sh")
    assert warn is None and b"\r\n" not in payload
    assert "echo 中文测试".encode("utf-8") in payload, "UTF-8 字节被改动/转码"
    assert not payload.startswith(codecs.BOM_UTF8), "凭空加 BOM"
    assert len(payload) == len(ZH_CRLF) - 2


def test_unix_sh_already_lf_is_idempotent():
    payload, warn = encode_script_bytes(LF_SH, False, "x.sh")
    assert payload == LF_SH and warn is None
    again, _ = encode_script_bytes(payload, False, "x.sh")
    assert again == payload


def test_lone_cr_control_char_is_kept():
    src = b"#!/bin/bash\r\nprintf 'a\rb'\r\n"
    payload, _ = encode_script_bytes(src, False, "x.sh")
    assert payload == b"#!/bin/bash\nprintf 'a\rb'\n", payload
    assert payload.count(b"\r") == 1, "裸 \\r 被当成行尾删掉了"


def test_mixed_endings_shell_family_and_shebang_dep():
    # 混合行尾：CRLF 与裸 LF 并存 → 全部 LF
    assert encode_script_bytes(b"#! /bin/sh\r\necho a\necho b\r\n", False, "x.sh")[0] == \
        b"#! /bin/sh\necho a\necho b\n"
    # shell 家族后缀（大小写不敏感）
    for name in ("a.bash", "a.BASH", "a.zsh", "a.ksh"):
        assert encode_script_bytes(b"echo x\r\n", False, name)[0] == b"echo x\n", name
    assert ".sh" in SHELL_SUFFIXES
    # 无后缀：带 shebang 的随传依赖照规整（内核直执时 shebang 行对 \r 最敏感）
    assert encode_script_bytes(b"#!/bin/sh\r\necho dep\r\n", False, "helper")[0] == \
        b"#!/bin/sh\necho dep\n"
    # 无后缀且无 shebang → 原样（不是脚本，不猜）
    assert encode_script_bytes(b"plain\ntext\r\n", False, "README")[0] == b"plain\ntext\r\n"


def test_unix_other_suffixes_untouched():
    # 注意：这里用的全是**不带 shebang**的内容；带 `#!` 的无后缀依赖由上一个测试覆盖
    for name in ("x.py", "x.ps1", "cfg.json", "data.bin", "requirements.txt", "archive.tar.gz", ""):
        for data in (b"echo hi\r\necho there\r\n", BIN_DEP, "k=1\r\n".encode("utf-8")):
            payload, warn = encode_script_bytes(data, False, name)
            assert payload == data and warn is None, (name, payload)


def test_windows_side_behavior_unchanged():
    payload, warn = encode_script_bytes(b"@echo off\r\necho ok\n", True, "c.bat")
    assert warn is None and payload.decode("cp936") == "@echo off\r\necho ok\r\n"
    ps, _ = encode_script_bytes(b'Write-Output "x"\r\n', True, "t.ps1")
    assert ps == codecs.BOM_UTF8 + b'Write-Output "x"\r\n'
    for name in ("x.sh", "x.py", "x.json"):
        payload, warn = encode_script_bytes(ZH_CRLF, True, name)
        assert payload == ZH_CRLF and warn is None, name


def test_put_script_encoded_unix_path_normalizes(tmp_path):
    class _FakeSFTP:
        def __init__(self):
            self.written = {}

        def open(self, path, mode):
            assert mode == "wb", mode
            outer = self

            class _F:
                def write(self, data):
                    outer.written[path] = data

                def close(self):
                    pass

            return _F()

    local = tmp_path / "x.sh"
    local.write_bytes(CRLF_SH)
    sftp = _FakeSFTP()
    assert put_script_encoded(sftp, local, "script_hub/3/x.sh", False) is None
    assert sftp.written["script_hub/3/x.sh"] == b"#!/bin/bash\necho hello\n"
    # 二进制依赖走同一条链，必须原样
    dep = tmp_path / "data.bin"
    dep.write_bytes(BIN_DEP)
    assert put_script_encoded(sftp, dep, "script_hub/3/data.bin", False) is None
    assert sftp.written["script_hub/3/data.bin"] == BIN_DEP


def test_to_lf_helpers_are_mirrors():
    assert _to_lf(b"a\r\nb\nc") == b"a\nb\nc"
    assert _to_lf(b"a\nb") == b"a\nb"
    assert _to_lf(_to_lf(b"a\r\nb")) == _to_lf(b"a\r\nb")
