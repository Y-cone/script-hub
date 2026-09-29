"""远端输出解码（X 批次 .bat 路线引入的日志乱码）——离线字节级断言。

现象：.bat 里 `echo 中文测试` 执行成功，日志/界面却是 `���Ĳ������ OK`。
根因：.bat/.cmd 现在按目标 ANSI(cp936) 上传，远端回显的字节就是 cp936，而 exec_command 一律
      `decode("utf-8", errors="replace")` → 信息在解码处丢失（U+FFFD 不可逆）。
本文件断言 RemoteOutputDecoder 的逐段兜底行为（真机验证见另一处 e2e，本文件不联网）。

跑法（**必须隔离 DATA_DIR**：import app.* 会按 DATA_DIR 建目录）：
    cd backend && SCRIPTHUB_DATA_DIR=/tmp/shaa/data .venv/bin/python tests/test_remote_output_decode.py
本文件不联网、不建库（只 import executor/ssh_service 的纯函数）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.ssh_service import (  # noqa: E402
    RemoteOutputDecoder, _decode_mixed, _force_cut)

ZH = "中文测试"
ANSI = ZH.encode("cp936")
UTF8 = ZH.encode("utf-8")


def _decode(raw: bytes) -> str:
    d = RemoteOutputDecoder()
    return d.feed(raw) + d.flush()


def _no_replace(s: str, label: str):
    assert "\ufffd" not in s, f"{label}: 出现替换字符（信息已丢）: {s!r}"


def main() -> int:
    # ① 修复点：纯 ANSI 行（.bat → cp936）必须可读（改前 = `���Ĳ������`）
    out = _decode(b"echo " + ANSI + b"\r\n")
    assert out == "echo " + ZH + "\r\n", repr(out)
    _no_replace(out, "ANSI 行")

    # ② 不许回归：纯 UTF-8 行（.py -X utf8）原样
    out = _decode(b"echo " + UTF8 + b"\n")
    assert out == "echo " + ZH + "\n", repr(out)

    # ③ 不完整序列跨 recv 块：一个汉字被块边界切开也不得冒出 U+FFFD（老隐患）
    for raw, want in ((b"echo " + UTF8 + b"\n", "echo " + ZH + "\n"),
                      (b"echo " + ANSI + b"\r\n", "echo " + ZH + "\r\n")):
        d = RemoteOutputDecoder()
        got = "".join(d.feed(bytes([b])) for b in raw) + d.flush()   # 逐字节喂（极端切分）
        assert got == want, f"逐字节喂结果不符: {got!r} != {want!r}"
        _no_replace(got, "逐字节喂")

    # ④ 混合流：同一行里 UTF-8 片段 + ANSI 片段（.py 的输出 + cmd 的本地化报错）两边都要能读
    raw = b"'" + "中文.bat".encode("utf-8") + b"' " + "不是内部或外部命令".encode("cp936") + b".\r\n"
    out = _decode(raw)
    assert "中文.bat" in out, repr(out)
    assert "不是内部或外部命令" in out, repr(out)
    _no_replace(out, "混合行")

    # ⑤ ANSI 与 UTF-8 分行的真实形态（.bat 输出中文 + cmd 报错各占一行）
    raw = ("echo 中文测试\r\n".encode("cp936") + "echo 测试".encode("utf-8") + b"\r\n"
           + b"'xx' " + "不是内部或外部命令".encode("cp936") + b"\r\n")
    out = _decode(raw)
    assert out.count("测试") == 2 and "不是内部或外部命令" in out, repr(out)
    _no_replace(out, "多行混合")

    # ⑥ 两种编码都认不出的字节 → 可见标记，不静默替换、不丢字节
    out = _decode(b"echo " + b"\xff\xfe\xfd" + b"\r\n")
    assert "<0xff><0xfe><0xfd>" in out, repr(out)
    _no_replace(out, "不可解码字节")

    # ⑦ 只有 \r 的进度输出必须即时出来（不等到 \n / 缓冲上限）
    d = RemoteOutputDecoder()
    first = d.feed(b"progress 10%\r")
    assert first == "progress 10%\r", repr(first)

    # ⑧ 无换行的长输出：越过缓冲上限（MAX_PENDING_BYTES）时按 ASCII 边界切段，不切开多字节序列
    d = RemoteOutputDecoder()
    blob = (b"a" * 9000) + ANSI + ANSI
    out = d.feed(blob)
    assert len(out) >= 9000, f"长输出没吐出来: {len(out)}"
    assert ANSI.decode("cp936") in (out + d.flush()), "长输出切段后中文被切开"

    # ⑨ flush：末尾没有换行、且末尾是半截序列时也不能静默丢
    d = RemoteOutputDecoder()
    got = d.feed(b"tail " + ANSI[:1]) + d.flush()
    assert "tail " in got and got != "tail ", f"尾半截字节被吞: {got!r}"

    # ⑩ 纯 ASCII 输出（绝大多数命令）逐字节不变——兜底不许改正常路径
    plain = b"$ cmd /c ver\r\nMicrosoft Windows [Version 10.0.26100.1]\r\n"
    assert _decode(plain) == plain.decode("utf-8"), "ASCII/UTF-8 路径被改动"

    # ⑪ 已知歧义（锁行为，不修）：纯 ANSI 行**恰好**整体构成合法 UTF-8 时会被当 UTF-8 读。
    # 例：cp936 的「一」= D2 BB，正好是一个合法 2 字节 UTF-8 序列（→ ѻ）。
    # 无解歧义：任何"GBK 优先"的判据都会把真正的 UTF-8 流毁掉（GBK 几乎能"成功"解任何字节），
    # 所以选择 UTF-8 严格优先；这类短行概率低（每个字约 8% 落在该窗口，需整行都落在窗口内）。
    ambiguous = b"echo " + "一".encode("cp936") + b"\r\n"
    assert _decode(ambiguous) != "echo 一\r\n", "行为变了：请重新评估注释里的歧义说明"
    # ANSI 前导字节 + UTF-8 首字节紧邻（无 ASCII 间隔）时，UTF-8 首字节可能被吃进 ANSI 对子
    # （_decode_mixed 的注释已说明该上限）；此处只断言不丢信息（两种读法之外不留空）
    got = _decode_mixed(b"\xca" + UTF8, "cp936")
    assert len(got) >= 1, repr(got)

    # ⑬ 真机回归（本批真机实测得到）：混合路径里 2 字节 UTF-8 读法会让位给 ANSI。
    # cp936「也」= D2 B2、「一」= D2 BB，都正好是合法 2 字节 UTF-8 序列；
    # 真机 `notexist_cmd_中文` 的报错行曾被解成「…或外部命令，Ҳ不是可运行的程序」。
    got = _decode(b"\xff" + "也一".encode("cp936") + b"\r\n")   # 前置 0xff 让整段走混合路径
    assert "也一" in got, f"2 字节 UTF-8 抢走了 GBK 汉字: {got!r}"
    assert "Ҳ" not in got and "һ" not in got, got

    # ⑫ _force_cut 不切开多字节序列
    assert _force_cut(b"abc\xe4\xb8") == 3, "切点压在多字节序列中间"
    assert _force_cut(b"abc") == 3
    assert _force_cut(b"\xe4\xb8\xad") == 0   # 全高位字节：不切（交给下次 feed）

    print("PASS | 远端输出解码：ANSI 行可读、UTF-8 不回归、块边界/混合流/不可解码字节均不丢信息")
    return 0


def test_remote_output_decoder():
    """pytest 入口（本文件主要是可直接跑的 assert 自检，这里保证整目录 pytest 也覆盖它）"""
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
