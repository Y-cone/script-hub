"""Windows 目标脚本上传编码自检（对应真机复现报告；本文件是离线字节级断言）。

跑法（**必须隔离 DATA_DIR**：import app.database 会按 DATA_DIR 建库，不要指向仓库 data/）：
    cd backend && SCRIPTHUB_DATA_DIR=/tmp/shx/data .venv/bin/python tests/test_win_script_encoding.py
（批次 AJ 起也由 `pytest tests` 收集执行，见文件末尾 test_main_selfcheck()）

覆盖：
 1) Unix 目标 / .py / .ps1 / .bat 等其他后缀 → 字节完全不变（转码必须是 no-op）；
    .sh（及带 shebang 的无后缀文件）只做 CRLF→LF 行尾规整、编码不变（见
    tests/test_unix_sh_line_endings.py，批次 AH）
 2) Windows .bat/.cmd → 系统 ANSI(cp936) + CRLF；中文往返无损（无 ? / U+FFFD）
 3) Windows .ps1 → UTF-8 BOM 开头、内容解回一致；已带 BOM 不重复加
 4) ANSI 编不下来的字符 → 保留 UTF-8 + 明确警告（不静默丢字符、不写坏文件）
 5) 代码页旋钮 SCRIPTHUB_WIN_ANSI_CODEC 生效（含非法值回落 cp936）
 6) put_script_encoded 落的盘字节 == encode_script_bytes 结果（假 SFTP 桩）
 7) 命令构造：.bat 分支不再 chcp 65001；shell=bash 覆盖仍保留；.py 仍走 -X utf8
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.executor import (  # noqa: E402
    WIN_ANSI_CODEC_ENV, encode_script_bytes, put_script_encoded, win_script_cmd)

BOM = b"\xef\xbb\xbf"
ZH_BAT = "@echo off\n@REM 案例 cleanup script\nmkdir \"中文目录\" 2>nul\necho done\n"


def _check_no_loss(decoded: str, expected: str, label: str):
    assert "\ufffd" not in decoded and "?" not in decoded, f"{label}: 出现替换字符 {decoded!r}"
    assert decoded == expected, f"{label}: 内容不一致\n got={decoded!r}\nwant={expected!r}"


def main() -> int:
    # 1) Unix 目标 / 非 shell 后缀：必须原样（.sh 的 CRLF 规整见 test_unix_sh_line_endings.py）
    for name in ("x.sh", "x.py", "a/b/x.bat", "x.ps1"):
        payload, warn = encode_script_bytes(ZH_BAT.encode(), False, name)
        assert payload == ZH_BAT.encode() and warn is None, f"Unix 目标 {name} 被改动"
    for name in ("x.sh", "x.py", "x.json", "requirements.txt", "x"):
        payload, warn = encode_script_bytes(ZH_BAT.encode(), True, name)
        assert payload == ZH_BAT.encode() and warn is None, f"Windows 目标非 bat/ps1 {name} 被改动"

    # 2) Windows .bat/.cmd → cp936 + CRLF
    for name in ("cleanup.bat", "utils/cleanup.cmd", "CLEANUP.BAT"):
        payload, warn = encode_script_bytes(ZH_BAT.encode(), True, name)
        assert warn is None, f"{name}: 不该有警告 {warn}"
        assert b"\r\n" in payload, f"{name}: 未转 CRLF"
        assert payload.count(b"\n") == payload.count(b"\r\n"), f"{name}: 仍有裸 LF"
        _check_no_loss(payload.decode("cp936"), ZH_BAT.replace("\n", "\r\n"), f"{name} cp936 往返")
    # 混合行尾规整 + 已 CRLF 幂等
    p1, _ = encode_script_bytes("a\r\nb\nc".encode(), True, "m.bat")
    assert p1.decode("cp936") == "a\r\nb\r\nc", p1
    # 文件自带的 UTF-8 BOM 必须剥掉（cmd 会把它当第一行命令的一部分）
    p2, w2 = encode_script_bytes(BOM + ZH_BAT.encode(), True, "bom.bat")
    assert not p2.startswith(BOM) and w2 is None, "BOM 未剥离"
    assert p2.decode("cp936").startswith("@echo off")

    # 3) Windows .ps1 → UTF-8 BOM（无 BOM 时加；已有则不重复）
    ps1 = "Write-Output \"中文测试\"\n".encode("utf-8")
    payload, warn = encode_script_bytes(ps1, True, "t.ps1")
    assert payload.startswith(BOM) and warn is None, "ps1 未加 BOM"
    assert payload[3:] == ps1 and payload.decode("utf-8-sig") == ps1.decode("utf-8")
    again, _ = encode_script_bytes(payload, True, "t.ps1")
    assert again == payload, "已带 BOM 的 ps1 被重复加 BOM"
    # ps1 不做行尾转换（PowerShell 接受 LF）
    assert b"\r\n" not in payload

    # 4) ANSI 编不下来的字符：保留 UTF-8 + 显式警告
    emoji = "echo 😀 中文\n"
    payload, warn = encode_script_bytes(emoji.encode("utf-8"), True, "emoji.bat")
    assert warn is not None and "警告" in warn, "未给出警告（静默降级）"
    _check_no_loss(payload.decode("utf-8"), emoji.replace("\n", "\r\n"), "emoji.bat 保留 UTF-8")
    assert "😀".encode("utf-8") in payload, "原字符被丢弃"
    # 非 UTF-8 源：不猜编码，原样保留 + 警告
    payload, warn = encode_script_bytes("echo 中文\n".encode("cp936"), True, "gbk_src.bat")
    assert warn is not None and payload == "echo 中文\r\n".encode("cp936"), "非 UTF-8 源处理错误"

    # 5) 代码页旋钮（用繁体样例：cp950 与 cp936 字节不同，能真正区分是否生效）
    ZH_TW = "@echo off\n@REM 繁體測試\necho 中文測試\n"
    os.environ[WIN_ANSI_CODEC_ENV] = "cp950"
    payload, warn = encode_script_bytes(ZH_TW.encode(), True, "k.bat")
    assert warn is None, warn
    assert payload == ZH_TW.replace("\n", "\r\n").encode("cp950") != \
        ZH_TW.replace("\n", "\r\n").encode("cp936"), "旋钮未生效"
    os.environ[WIN_ANSI_CODEC_ENV] = "no_such_codec"
    payload, warn = encode_script_bytes(ZH_TW.encode(), True, "k.bat")
    assert warn is None and payload == ZH_TW.replace("\n", "\r\n").encode("cp936"), "非法值未回落 cp936"
    os.environ.pop(WIN_ANSI_CODEC_ENV, None)
    payload, _ = encode_script_bytes(ZH_BAT.encode(), True, "k.bat")
    assert payload == ZH_BAT.replace("\n", "\r\n").encode("cp936"), "默认应为 cp936"

    # 6) put_script_encoded：读本地 → 转码 → 写远端（两处调用点的共同路径）
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

    with tempfile.TemporaryDirectory() as td:
        local = Path(td) / "cleanup.bat"
        local.write_text(ZH_BAT, encoding="utf-8")
        sftp = _FakeSFTP()
        warn = put_script_encoded(sftp, local, "script_hub/8/cleanup.bat", True)
        assert warn is None and sftp.written["script_hub/8/cleanup.bat"] == \
            ZH_BAT.replace("\n", "\r\n").encode("cp936"), "落盘字节与转码函数不一致"
        local2 = Path(td) / "emoji.bat"
        local2.write_text(emoji, encoding="utf-8")
        assert put_script_encoded(_FakeSFTP(), local2, "script_hub/8/emoji.bat", True) is not None

    # 7) 命令构造回归
    bat_cmd = win_script_cmd("bat", "script_hub/8/cleanup.bat", "", "script_hub/8")
    assert "chcp" not in bat_cmd and "call" in bat_cmd, bat_cmd
    assert "@echo" not in bat_cmd
    # N6（批次 BM）：Shell 覆盖只能同族——.bat + bash 此前被放行成 `bash hello.bat`，现在必须拒绝
    try:
        win_script_cmd("bat", "script_hub/8/x.bat", "", "script_hub/8", has_bash=True, shell="bash")
    except ValueError:
        pass
    else:
        raise AssertionError(".bat + shell=bash 未被拒绝（跨类型覆盖）")
    assert "chcp" not in win_script_cmd("bat", "script_hub/8/x.bat", "", "script_hub/8",
                                        shell="cmd"), "shell=cmd 覆盖分支不该带 chcp"
    assert "chcp 65001" in win_script_cmd("shell", "script_hub/8/x.sh", "", "script_hub/8",
                                          has_bash=True), ".sh 分支行为被改动"
    assert "-X utf8" in win_script_cmd("python", "script_hub/8/x.py", "", "script_hub/8")

    print("PASS | Windows 脚本上传编码：bat→cp936+CRLF, ps1→UTF-8 BOM, 其他原样, "
          "旋钮", WIN_ANSI_CODEC_ENV, "默认 cp936")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def test_main_selfcheck(monkeypatch):
    """把原本只走 __main__ 的自检接回 pytest 收集（此前 pytest 从不执行本文件，里面的
    断言改了坏了也没人知道）。返回 0 = 全过；失败时 main() 内部直接抛 AssertionError。

    monkeypatch 兜住 main() 对 SCRIPTHUB_WIN_ANSI_CODEC 的设置/清理：中途失败也不会把
    旋钮泄漏给同进程的其它测试。无其它副作用（临时目录 / 假 SFTP 桩 / 纯内存构造）。
    """
    monkeypatch.delenv(WIN_ANSI_CODEC_ENV, raising=False)
    assert main() == 0
    assert WIN_ANSI_CODEC_ENV not in os.environ
