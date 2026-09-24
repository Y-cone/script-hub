"""脚本源码的文本编码：读取探测 + 按原编码写回（堵「静默乱码 → 写坏源文件」）。

批次 AA 引入，原在 routers/script.py；批次 AC 移到这里：除路由（读/写源码接口）外，
services/parser.py 也要按探测编码读源码——service 反向 import router 是分层倒挂，
且 routers/script.py 顶部就 `from ..services.parser import parse_script`，会成循环导入。

旧行为（AA 修掉的）：读 UTF-8 失败就 latin-1（能解任意字节、永不报错）→ 界面显示乱码却"成功"，
用户一点保存就以 UTF-8 写回磁盘，GBK 源文件被永久损坏。规则：
  1) 有序探测（UTF-8 → 目标 ANSI（复用 X 的旋钮，默认 cp936）→ gb18030），全失败**明确报错**；
  2) 命中编码随读取接口返回（UI 可提示"当前按 GBK 解读"），不静默；
  3) 保存按**同一编码**写回；原编码表达不了的字符**明确报错**，绝不静默换成 `?`；
  4) 保留显式覆盖入口（保存请求带 encoding）；
  5) 保存时**复原磁盘原文件的行尾**（批次 AJ，见 preserve_eol）——编辑框（textarea）会把
     CRLF 规范化成 LF，不还原就会让磁盘文件的行尾在每次编辑后悄悄漂移。
"""
import codecs

_EXTRA_CODECS = ("gb18030",)   # 常见中文编码兜底；目标 ANSI 由旋钮 SCRIPTHUB_WIN_ANSI_CODEC 决定


def _encoding_candidates(raw: bytes) -> list[str]:
    """有序候选编码。UTF-8 必须最前：它的合法字节序列最"窄"，几乎不会把 GBK 文本误判成 UTF-8；
    反过来 GBK 解码器能"成功"解出 UTF-8 字节（产出乱码），所以 ANSI 只能排在 UTF-8 之后。
    带 UTF-8 BOM → 直接 utf-8-sig（BOM 不进文本，写回仍带 BOM，字节往返一致）。"""
    if raw.startswith(codecs.BOM_UTF8):
        return ["utf-8-sig"]
    from .executor import _win_ansi_codec
    return list(dict.fromkeys(["utf-8", _win_ansi_codec(), *_EXTRA_CODECS]))


def probe_script_text(raw: bytes) -> tuple[str, str]:
    """探测脚本字节的文本编码，返回 (文本, 命中编码)；全部失败 → ValueError（不静默降级）。"""
    if b"\x00" in raw:
        # 含 NUL → 二进制或 UTF-16 文本：任何单字节编码都能"成功"解出乱码，而乱码一旦保存就
        # 写坏文件，所以这里直接拒绝，不猜
        raise ValueError("疑似二进制或 UTF-16 文件（含 NUL 字节），不支持在线编辑")
    for enc in _encoding_candidates(raw):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise ValueError(
        "无法识别文件编码（既不是 UTF-8，也不是目标 ANSI/GB18030）；为避免显示乱码后写坏源文件，已拒绝打开。"
        "如确需编辑，请先在编辑器里转成 UTF-8。"
    )


# ── 行尾保全（批次 AJ）────────────────────────────────────────────────────────
# HTML <textarea> 按规范把内容里的 \r\n 规范化成 \n（浏览器行为，不是本仓库的代码）：用户一进
# 编辑态，CRLF 就已经没了，保存时不还原就会把磁盘上的 CRLF 文件悄悄写成 LF。真机复现：
# data/scripts/cleanup.bat 编辑后 108 → 107 字节。执行不受影响（上传层
# executor.encode_script_bytes 会按目标平台再转一次行尾），坏的是磁盘内容在无人察觉时漂移。


def disk_eol(raw: bytes) -> bytes | None:
    """磁盘原文件的行尾样式：只有单一稳定行尾才返回（b"\\r\\n" / b"\\n"），其余 None。

    混合行尾（既有 \\r\\n 又有裸 \\n）与无换行返回 None —— 不替用户"规整"：那是在改内容
    而不是保留内容（同上，executor 的 _to_crlf/_to_lf 只在目标平台必须时才动行尾）。
    """
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    if crlf and not lf:
        return b"\r\n"
    if lf and not crlf:
        return b"\n"
    return None


def preserve_eol(raw: bytes, data: bytes) -> bytes:
    """把待写入字节的行尾还原成磁盘原文件的样式（原文件不是 CRLF → 原样返回）。

    先把 \\r\\n 归一成 \\n 再统一转 \\r\\n，混合输入也不会产出 \\r\\r\\n。
    副作用（正是我们想要的）：原文件 CRLF + 内容未改时，还原后与 raw 逐字节相同 →
    调用方的"字节相同则不写盘"判定照旧生效，不会因为这次转换白白重写一遍文件。
    """
    if disk_eol(raw) != b"\r\n":
        return data
    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def _encoding_label(enc: str) -> str:
    """编码的可读名（cp936/cp950 显示成 GBK/BIG5，便于用户看懂提示）"""
    name = enc.lower()
    if name in ("cp936", "gbk", "gb2312", "ms936"):
        return "GBK(cp936)"
    if name in ("cp950", "big5"):
        return "BIG5(cp950)"
    return enc
