"""SSH 服务：paramiko 连接管理 + 设备级 session 缓存 + 远程执行 + 远端环境探测

架构说明：
- 连接按设备缓存（LRU，会话有效期内复用，减少握手）
- 远程执行输出逐行通过回调送回（由调用方写 DB → WS 轮询推送）
- 本文件不含 DB/业务逻辑，只做 SSH 网络层
"""
import io
import logging
import asyncio
import time
from collections import OrderedDict
from typing import Callable, Optional, Any
import paramiko

from ..models.device import Device
from ..services.secret_store import get_secret

logger = logging.getLogger(__name__)


def _service_name(device_id: int) -> str:
    return f"scripthub-device-{device_id}"


def _load_password(device: Device):
    """读取设备密码（keyring/文件）"""
    return get_secret(_service_name(device.id), device.username)


def _load_key(device: Device):
    """读取设备私钥内容（keyring/文件），解析为 paramiko 私钥对象"""
    key_str = get_secret(_service_name(device.id), device.username)
    if not key_str:
        return None
    for cls in (paramiko.RSAKey, paramiko.ECDSAKey, paramiko.Ed25519Key):
        try:
            return cls.from_private_key(io.StringIO(key_str))
        except (paramiko.SSHException, ValueError, TypeError):
            continue
    return None


def build_client(device: Device, timeout: float = 10.0) -> paramiko.SSHClient:
    """建立 SSH 连接（供测试/执行复用）"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kw = {
        "hostname": device.host,
        "port": device.port,
        "username": device.username,
        "timeout": timeout,
        "banner_timeout": timeout,
        "auth_timeout": timeout,
    }
    if device.auth_type == "key":
        pkey = _load_key(device)
        kw["pkey"] = pkey
        if pkey is None:
            raise paramiko.AuthenticationException("无法读取该设备的私钥凭据")
    else:
        pw = _load_password(device)
        kw["password"] = pw
    client.connect(**kw)
    return client


class DeviceSessionPool:
    """设备级 paramiko 客户端池（LRU，最多 8 个会话）"""

    def __init__(self, maxsize: int = 8):
        self._pool: OrderedDict[int, paramiko.SSHClient] = OrderedDict()
        self._max = maxsize
        self._lock = asyncio.Lock()

    async def get(self, device: Device) -> paramiko.SSHClient:
        """获取设备连接：命中缓存复用，否则新建"""
        async with self._lock:
            client = self._pool.get(device.id)
            if client is not None and client.get_transport() and client.get_transport().is_active():
                self._pool.move_to_end(device.id)
                return client
            # 失效或不存在 → 新建（在事件循环外做阻塞连接）
        client = await asyncio.to_thread(build_client, device)
        async with self._lock:
            self._pool[device.id] = client
            self._pool.move_to_end(device.id)
            if len(self._pool) > self._max:
                _, oldest = self._pool.popitem(last=False)
                try:
                    oldest.close()
                except Exception:
                    pass
        return client

    async def close(self, device_id: int):
        async with self._lock:
            client = self._pool.pop(device_id, None)
        if client:
            try:
                client.close()
            except Exception:
                pass

    async def invalidate(self):
        async with self._lock:
            items = list(self._pool.values())
            self._pool.clear()
        for c in items:
            try:
                c.close()
            except Exception:
                pass


pool = DeviceSessionPool()


# ── 远端输出解码：字节流 → str（X 批次「GBK 路线」引入的日志乱码，修在这一层）───────────────
# 现象：.bat 里 `echo 中文测试` 执行成功，但日志/界面显示 `���Ĳ������ OK`。
# 根因：.bat/.cmd 现在按目标 ANSI 上传（见 executor.encode_script_bytes），远端回显的字节就是
# 系统 ANSI（默认 cp936），这里却一律 `decode("utf-8", errors="replace")` → **信息在解码处就丢了**
# （U+FFFD 不可逆），后置任何处理都救不回来。所以兜底只能做在解码点，且这是 exec_command 的
# 内部逻辑（所有远程执行/探测都经由它），改一处即覆盖全部调用者。
#
# 难点：远端输出常是**混合流**——同一行里既有 UTF-8 字节（`python -X utf8` 的输出、SSH 侧原样
# 转发的命令行回显），也有 ANSI 字节（cmd / powershell 自身的本地化报错文案）。两种粗暴判据都会
# 把其中一半弄坏：「见 U+FFFD 就整段按 ANSI 重解」会因一个坏字节把整行正常 UTF-8 中文毁了；
# 「整段 UTF-8 失败就整段换 ANSI」会把混合行里的 UTF-8 片段解成乱码。
#
# 采用的策略（两级，见 RemoteOutputDecoder）：
#   1) **切段**：按 \n / \r 切（保留分隔符；\r 覆盖进度条，保证实时性），跨 recv() 块的
#      不完整多字节序列留在 pending 里 —— 顺带修掉一个既有隐患：此前每块各自 errors="replace"，
#      一个汉字正好被 4096 字节边界切开就会凭空多出一个 U+FFFD。
#   2) **段内**：先试严格 UTF-8（纯 UTF-8 / 纯 ASCII 段零成本、逐字节精确，绝不误伤）；
#      失败才做逐字节混合解码（_decode_mixed）：1/3/4 字节的 UTF-8 序列按 UTF-8 吃，
#      其余按目标 ANSI（复用 X 的旋钮，默认 cp936）吃 1~2 字节——**2 字节的 UTF-8 读法
#      一律让给 ANSI**（GBK 汉字约 8% 与合法 2 字节 UTF-8 序列撞车，真机实测会吃掉「也」变 Ҳ，
#      详见 _decode_mixed 的注释）；两种都认不出的字节落成可见标记 `<0xff>`，
#      **不丢字节、不静默 replace**。
MAX_PENDING_BYTES = 8192   # 段内无换行时的上限；超过则退到最近 ASCII 边界强制切段（保实时）


def _utf8_lead_len(b: int) -> int:
    """UTF-8 首字节声明长度（1/2/3/4）；不是合法首字节 → 0。"""
    if b < 0x80:
        return 1
    if 0xC2 <= b <= 0xDF:
        return 2
    if 0xE0 <= b <= 0xEF:
        return 3
    if 0xF0 <= b <= 0xF4:
        return 4
    return 0


def _utf8_tail_len(raw: "bytes | bytearray") -> int:
    """raw 尾部是否是一段**尚未收齐**的 UTF-8 序列：是 → 返回已到的字节数（1~3），否则 0。

    供 RemoteOutputDecoder.drain 用：交互终端不能等换行，但也不能把半截序列解出去
    （解出去就是一个 U+FFFD 或乱码，且不会自愈）。
    """
    for k in (1, 2, 3):
        if k > len(raw):
            break
        n = _utf8_lead_len(raw[len(raw) - k])
        if n and n > k and all(0x80 <= x <= 0xBF for x in raw[len(raw) - k + 1:]):
            return k
    return 0


def _utf8_seq_len(raw: bytes, i: int) -> int:
    """raw[i:] 起是否是一段**完整合法**的 UTF-8 序列；是返回其字节数，否则 0（含尾半截）。"""
    b = raw[i]
    if b < 0x80:
        return 1
    if 0xC2 <= b <= 0xDF:
        n = 2
    elif 0xE0 <= b <= 0xEF:
        n = 3
    elif 0xF0 <= b <= 0xF4:
        n = 4
    else:
        return 0
    if i + n > len(raw):
        return 0
    try:
        raw[i:i + n].decode("utf-8")
    except UnicodeDecodeError:
        return 0
    return n


def _decode_mixed(raw: bytes, ansi: str) -> str:
    """段内混合解码：UTF-8 片段与 ANSI 片段可按字节级交错，各自按能认的读法吃。

    **只认 1 字节(ASCII) / 3 字节 / 4 字节的 UTF-8 序列，2 字节的读法让给 ANSI。**
    为什么（真机实测得出，不是纸面推理）：GBK 汉字约 8% 落在「合法 2 字节 UTF-8 序列」窗口里
    （如 cp936 的「也」= D2 B2，正好解成 U+04B2 Ҳ）。若逐字节「UTF-8 优先」，一条纯 ANSI 中文行
    里几乎必有 1~2 个字被抢走成拉丁/西里尔字符——真机 `notexist_cmd_中文` 的报错行就出现
    「不是内部或外部命令，Ҳ不是可运行的程序」。3/4 字节序列（CJK/emoji）要跨 1.5~2 个 GBK 汉字
    才误判，概率低几个数量级；而真正的 UTF-8 片段若只含 2 字节字符，其所在段本身就是纯 UTF-8
    （纯 UTF-8 段由严格 UTF-8 先接走，根本不会进这里），所以牺牲面很小。

    已知上限（可接受）：混合段里的 2 字节 UTF-8 字符（é/西里尔等）会被按 ANSI 读；ANSI 前导字节
    后紧跟 UTF-8 首字节且两者构成合法 GBK 对时，该首字节会被 ANSI 吃掉。字节都没丢（仍在输出里）。
    """
    out: list[str] = []
    run = bytearray()   # 累积中的 UTF-8 片段

    def _flush_run():
        if run:
            out.append(run.decode("utf-8"))
            run.clear()

    i, n = 0, len(raw)
    while i < n:
        ln = _utf8_seq_len(raw, i)
        if ln and ln != 2:      # ASCII / 3 字节 CJK / 4 字节 emoji → 按 UTF-8 吃
            run += raw[i:i + ln]
            i += ln
            continue
        _flush_run()
        for k in (2, 1):    # ANSI：GBK 双字节优先，退化到单字节
            if i + k <= n:
                try:
                    out.append(raw[i:i + k].decode(ansi))
                    i += k
                    break
                except UnicodeDecodeError:
                    continue
        else:
            out.append(f"<0x{raw[i]:02x}>")   # ANSI 也认不出：可见标记，不丢字节
            i += 1
    _flush_run()
    return "".join(out)


def _force_cut(buf: "bytes | bytearray") -> int:
    """强制切点：退到最后一个 ASCII 字节之后（不切开多字节序列）；整段无 ASCII → 不切。"""
    i = len(buf) - 1
    while i >= 0 and buf[i] >= 0x80:
        i -= 1
    return i + 1


class RemoteOutputDecoder:
    """远端输出字节流 → str 的有状态解码器（逐段温和兜底，不丢信息）。

    `feed(chunk)` 返回「现在就能定下来」的文本，不完整的尾部字节留到下次；流结束调 `flush()`。
    """

    def __init__(self, ansi_codec: Optional[str] = None):
        if ansi_codec is None:
            # 复用 X 的旋钮（SCRIPTHUB_WIN_ANSI_CODEC，默认 cp936）——不新造一套编码配置
            from .executor import _win_ansi_codec
            ansi_codec = _win_ansi_codec()
        self.ansi = ansi_codec
        self._pending = bytearray()

    def _segment(self, raw: bytes) -> str:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return _decode_mixed(raw, self.ansi)

    def feed(self, data: bytes) -> str:
        if not data:
            return ""
        self._pending += data
        out: list[str] = []
        start = 0
        for idx, b in enumerate(self._pending):
            if b in (0x0A, 0x0D):   # \n / \r 都是段边界（\r 供进度条即时回显）
                out.append(self._segment(bytes(self._pending[start:idx + 1])))
                start = idx + 1
        del self._pending[:start]
        if len(self._pending) >= MAX_PENDING_BYTES:
            cut = _force_cut(self._pending)
            if cut:
                out.append(self._segment(bytes(self._pending[:cut])))
                del self._pending[:cut]
        return "".join(out)

    def flush(self) -> str:
        """流结束：把 pending 里剩下的字节解出来。"""
        if not self._pending:
            return ""
        seg = self._segment(bytes(self._pending))
        self._pending.clear()
        return seg

    def drain(self) -> str:
        """**交互终端专用**：把当前能定下来的字节立刻产出（不等换行）。

        feed() 只按 \\n / \\r 切段，而交互提示符（`C:\\Users\\x>`）**没有换行**——只走 feed 的话
        提示符会一直卡在 pending 里，用户看不到首屏，直到他敲下一个键才有输出。
        这里把 pending 全解出去，只留**尚未收齐的 UTF-8 序列**（1~3 字节），保证不切出半个汉字。
        已知上限（可接受）：目标 ANSI（cp936）汉字正好被 recv() 块边界切成两半时，前半段会显示成
        `<0x??>` 可见标记——不丢字节、不静默 replace，但也不会被重新解码拼回去。
        """
        if not self._pending:
            return ""
        hold = _utf8_tail_len(self._pending)
        cut = len(self._pending) - hold
        if cut <= 0:
            return ""
        seg = self._segment(bytes(self._pending[:cut]))
        del self._pending[:cut]
        return seg


# ── 交互终端「输入」方向的编码（③ 双向乱的另一半）────────────────────────────────────
# 输出方向由 RemoteOutputDecoder 兜底；输入方向此前一律 `data.encode("utf-8")` —— 对远端
# Windows 的控制台是错的：控制台按自己的**代码页**（默认系统 ANSI cp936）读键盘输入，
# 发过去的 UTF-8 中文会被解释成乱码。但控制台代码页取决于会话是怎么起的（见下），
# 且控制序列必须逐字节透传，不能整体丢进代码页编码器。


def terminal_input_codec(is_windows: bool, shell: str = "") -> Optional[str]:
    """交互终端的**输入/预期回显**代码页：None = UTF-8 直通（= 旧行为），否则目标 ANSI 名。

    Windows 目标要看会话建连方式（session_manager.RemotePTYSession._open）：
    - 默认 shell（invoke_shell）→ 建会话时已注入 `chcp 65001`，控制台就是 UTF-8 → 直通；
    - 显式 cmd / powershell → 未动代码页，仍是系统 ANSI（cp936…）→ 中文输入须按它编码；
    - Git Bash → msys 自带 UTF-8 口径 → 直通。
    非 Windows 远端 → 直通（Linux/macOS 本机与远端都是 UTF-8，不要误伤）。
    """
    if not is_windows:
        return None
    if (shell or "").strip().lower() in ("", "bash"):
        return None
    from .executor import _win_ansi_codec
    return _win_ansi_codec()


def encode_terminal_input(text: str, ansi_codec: Optional[str] = None) -> bytes:
    """交互终端**发送**方向：可见文本按目标代码页编码，控制序列逐字节原样透传。

    为什么必须分开：`\\x1b[A`（方向键）、`\\x1b`（ESC）、`\\x03`（Ctrl-C）是**按字节约定**的
    终端控制序列，交给代码页编码器只能得到一个 `?`（cp936 里 U+001B 这类控制字符不在表内，
    不同 Python 版本/编解码器行为还不一致）。所以按字符切：控制字节（< 0x20 与 0x7F）原样
    透传，其余按代码页编码。`ansi_codec=None` = UTF-8 直通（旧行为，Linux/macOS 远端与
    Windows 默认 shell 会话）。
    已知上限：目标代码页表示不了的字符（emoji 等）在**输入**侧落成 `?` —— 这里不能像写文件
    那样明确报错（不能因为一个字符把整次按键丢掉）；写文件那条路的规则见
    routers/script.py 的 probe_script_text（那里必须明确失败）。
    """
    if ansi_codec is None:
        return text.encode("utf-8")
    out = bytearray()
    buf: list[str] = []      # 累积中的可见文本（连续编码，避免逐字符调用 codec）

    def _flush():
        if buf:
            out.extend("".join(buf).encode(ansi_codec, errors="replace"))
            buf.clear()

    for ch in text:
        o = ord(ch)
        if o < 0x20 or o == 0x7F:
            _flush()
            out.append(o)    # 控制字节原样（ESC/CSI/Ctrl-C/Tab/回车…）
        else:
            buf.append(ch)
    _flush()
    return bytes(out)


async def exec_command(
    device: Device,
    command: str,
    timeout: float = 120,
    output_cb: Optional[Callable[[str], Any]] = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> tuple[int, str]:
    """在远端执行命令。output_cb 每段输出回调（可为异步函数）；返回 (exit_code, 完整输出)"""
    client = await pool.get(device)
    chan = client.get_transport().open_session(timeout=15)
    chan.settimeout(timeout)
    chan.exec_command(command)

    collected = ""
    code = 1

    def _reader_blocking():
        """同步读取 channel 到结束（在 asyncio.to_thread 中跑，避免阻塞事件循环）。
        循环直到 exit_status_ready 且缓冲区读空（无残留输出）。
        带总时限兜底：即使 exit_status_ready 迟迟不触发（如远端半关闭），超时强制收敛。
        """
        nonlocal collected
        import time as _t
        # 有状态解码器：UTF-8 失败按目标 ANSI 兜底（修 X 批次 .bat 路线引入的日志乱码，
        # 详见 RemoteOutputDecoder 的注释）。跨 recv 块的不完整序列由它自己缓冲。
        dec = RemoteOutputDecoder()
        deadline = _t.time() + timeout + 10
        while True:
            if cancel_event and cancel_event.is_set():
                try:
                    chan.close()  # 关闭通道 → 远端进程随会话终止
                except Exception:
                    pass
                break
            # 优先读尽当前缓冲
            if chan.recv_ready() or chan.recv_stderr_ready():
                try:
                    data = chan.recv(4096)
                    if not data:
                        data = chan.recv_stderr(4096)
                except Exception:
                    data = b""
                if data:
                    collected += dec.feed(data)
                    continue  # 读到真实数据则继续读
                # 读到的为空（EOF），跳出循环走 exit 检查，避免死循环
            # 缓冲区空且已结束 → 完成
            if chan.exit_status_ready():
                break
            if _t.time() > deadline:
                try:
                    chan.shutdown(1)  # 强制关闭接收，促使 exit_status 收敛
                except Exception:
                    pass
                break
            _t.sleep(0.05)
        collected += dec.flush()   # 末行没换行/尾半截字节也要出来（不许静默丢尾巴）

    # 同步读完整输出（线程中），期间按段回调
    def _run():
        _reader_blocking()
        try:
            return chan.recv_exit_status()
        except Exception:
            return -1

    try:
        code = await asyncio.to_thread(_run)
        # 逐段回调完整输出（模拟实时；小脚本一次性回传足够）
        if output_cb and collected:
            res = output_cb(collected)
            if asyncio.iscoroutine(res):
                await res
    finally:
        try:
            chan.close()
        except Exception:
            pass
    return code, collected


# 远端环境探测命令（复用 envcheck 的探测逻辑，命令走 SSH 在远端执行）
# 分平台：先首探平台，再按平台分发第二段探测命令，避免跨平台误报
REMOTE_PROBE_CMDS_UNIX = {
    "uname": "uname -s",
    "python": "python --version 2>&1",
    "python3": "python3 --version 2>&1",
    "node": "node --version 2>&1",
    "bash": "bash --version 2>&1 | head -1",
    # 仅探测执行脚本所需运行时（.py/.js/.sh）；git/java 与执行路径无关，不探测
}
REMOTE_PROBE_CMDS_WIN = {
    "python": "python --version 2>&1",
    "node": "node --version 2>&1",
    "powershell": "powershell -NoProfile -Command \"$PSVersionTable.PSVersion.ToString()\" 2>&1",
    # bash：把 Git Bash 常见安装路径并入 PATH 再探测（cmd 默认 PATH 不含 Git\bin）；
    # 未装 Git Bash 时 bash not recognized → _installed_from_out 判 False
    "bash": 'cmd /c "set PATH=%PATH%;C:\\Program Files\\Git\\bin;C:\\Program Files (x86)\\Git\\bin& bash --version 2>&1"',
    # 不做 uname/bash/python3（避免 Windows 上误报缺失）；os 信息由 remote_probe 单独取 %OS%
}

# 首探命令：Windows 有 cmd 且 ver 成功→echo WIN；Unix 无 cmd→echo UNX。
# 用退出码+固定 ASCII 标记判平台，避免依赖系统语言/编码（GBK 乱码）解析。
_PLATFORM_PROBE = "cmd /c ver >nul 2>&1 && echo WIN || echo UNX"


async def detect_platform(device: Device) -> str:
    """轻量探测远端平台：仅跑首探命令（含 WIN/UNX 标记），返回 'win32' / 'unix'。"""
    try:
        _, out = await exec_command(device, _PLATFORM_PROBE, timeout=10)
        return "win32" if "WIN" in out.upper() else "unix"
    except Exception:
        return "unix"


async def is_win_device(device: Device) -> bool:
    """设备是否 Windows —— **手动执行 / 定时下放 / 交互终端共用的唯一平台判据**。

    一律走实时探测（detect_platform，复用池内连接，不额外握手），**不读 device.type**：
    SPEC 注明该字段「仅 UI 区分」，可能标错/过期；标错时手动执行探测正确、下放走错 runner，
    两套判据就是下一个 bug。探测失败回落 "unix"（与手动执行既有行为一致）。
    """
    return (await detect_platform(device)) == "win32"


# ── 设备 probe 结果内存缓存（SPEC §7.2-#2 展示字段的来源）─────────────────────────────
# 为什么在 service 层而不在 routers/device.py：读穿透的 `detect_has_bash`（下）与列表接口
# 要读同一份结论。留在 router 里就只能让 service 反向 import 一个 router 的私有全局
# （批次 AM 否掉这条路）→ 搬到两边都能往下依赖的 service，方向才是正的。
# ponytail: 进程内存，重启即失（前端降级显示「未探测」）；多实例不共享，可接受。
_probe_cache: dict[int, dict] = {}

# 批次 AM：300 → 600。切设备会自动重探（前端 reprobe）→ 缓存新鲜度不再只靠 TTL 兜底，
# 放宽只是少探几次（每次 Windows .sh 执行/下放都要过一次 detect_has_bash）。
# 代价：卸载 Git Bash 后旧结论（绿）最多多显示 5 分钟 —— 用户已接受的取向（「执行报错就好」）。
CACHE_TTL = 600  # 秒


def cache_probe(device_id: int, *, ok: bool, os_info: str | None = None,
                latency_ms: int | None = None, error: str | None = None,
                runtimes: list[dict] | None = None,
                platform: str | None = None) -> None:
    """test/probe 后调用，供列表接口回填展示字段（批次 AL：合并式写入）。

    合并规则 —— **None 表示「本次没探这项」→ 保留旧值**，显式传值（含 `[]`）表示
    「本次结论」→ 覆盖。三个写入点的差别只在这里：
      · `ok=True`  → 逐字段合并。本次没探的项沿用旧结论（如 /{id}/probe 不测握手延时，
                     不该把上次 test 的 latency 冲成 None；平台探测失败的 test 同理）。
      · `ok=False` → 代表「这台机器现在够不着」→ **整条结论作废**（os_info/latency/runtimes/
                     platform 全清），只留 error。不这么做就会出现「设备连不上，UI 却说装了 bash」。
      · 旧条目已过 TTL → 按「没探过」处理：不给过期结论续命（与列表接口的 TTL 语义一致）。
    ⚠️ 已知取舍：合并在 ok=True 时会把旧值的时间戳一起刷新（缓存只存一个 ts，没有逐字段时间戳），
    所以「平台探测连续失败 + 用户反复点测试连接」能让上次的 runtimes 一直活着。
    ponytail: 单 ts 够用；真出问题再改成逐字段 ts（{值, ts}），别提前上。

    批次 AK①：`runtimes` = 该次 remote_probe()["runtimes"] 的**原文**（不另造摘要结构，
    与 GET /api/devices/{id}/probe 返回的 runtimes 完全同形）；「探了但没结论」传 `[]`，
    别传 None（None 在 AL 之后是「没探」的意思）。
    粒度选「原文数组」而非 `{"bash": true}` 摘要：① 前端要判定的运行时由
    envcheck.ALT_RUNTIME_RULES 决定（目前只有 shell→bash），改成摘要就得在后端维护一份
    与 probe 端点不同的形状，两处迟早不同步；② 一次探测 5 条小 dict，体积可忽略。

    批次 AO：`platform` 同上取 remote_probe()["platform"] 的**原文**（win32/unix/windows）。
    为什么要进缓存：设备上下文下的环境检测（GET /api/scripts/{id}/env-check?device_id=X）
    必须按**目标设备**的平台判定，而 probe 端点自己探到的那个值以前只随响应回家、不留档 →
    只剩「再探一次」或「按 device.type 猜」两条路（前者卡页面，后者字段可能标错）。
    读取方 = envcheck.platform_family ×1 处归一化（PLATFORM_RULES 只认 windows/unix）。

    批次 AM：调用的地方多了一处 —— `detect_has_bash` 的读穿透（只写 runtimes，别的项留 None）。
    """
    now = time.monotonic()
    c = _probe_cache.get(device_id)
    # ok=False（连不上）或旧结论已过期 → 没有可继承的结论，从空开始
    prev = c if (c and ok and now - c["ts"] <= CACHE_TTL) else {}
    _probe_cache[device_id] = {
        "ts": now, "ok": ok,
        "os_info": os_info if os_info is not None else prev.get("os_info"),
        "latency_ms": latency_ms if latency_ms is not None else prev.get("latency_ms"),
        "error": error,
        "runtimes": runtimes if runtimes is not None else prev.get("runtimes"),
        "platform": platform if platform is not None else prev.get("platform"),
    }


def fresh_entry(device_id: int) -> dict | None:
    """**新鲜**（未过 TTL）的缓存条目；没探过 / 已过期 → None。

    新鲜度判定只此一处（各调用方都别再各写一遍 `now - ts <= TTL` 然后悄悄不同步）：
    `routers/device.list_devices`（展示字段回填）、`routers/script` 的环境检测端点、
    读穿透 `detect_has_bash` / `_write_bash_runtime`。TTL 语义见 CACHE_TTL。
    """
    c = _probe_cache.get(device_id)
    if not c or time.monotonic() - c["ts"] > CACHE_TTL:
        return None
    return c


async def detect_has_bash(device: Device) -> bool:
    """探测 Windows 远端是否可执行 bash（Git Bash/MSYS2/WSL）—— **读穿透 probe 缓存**（批次 AM）。

    判据与 remote_probe 完全一致（REMOTE_PROBE_CMDS_WIN["bash"]：cmd PATH 并入 Git Bash
    常见安装路径后 `bash --version`），但结论不再单独存在 `_BASH_CACHE` 里：

      ① 缓存新鲜（未过 TTL）且里面有 bash 条目 → 直接用，零额外开销；
      ② 未命中 / 已过期 / 缓存里没有 bash 条目 → 现探一次，**结论写回同一份 probe 缓存**
         （见 _write_bash_runtime）→ 列表接口与脚本库三态跟着变新，不必等 TTL 到期或重启；
      ③ 探测本身失败（连不上 / 超时 / 一点输出都没拿到）→ **不写缓存**，返回 True。

    ③ 为什么返回 True：调用方（executor / sched_delegate）拿到 False 时，win_script_cmd 只会
    构造一条「[边界] 未检测到 Bash」的拒绝命令 —— 即**误拒一台其实装了的机器**，正是本批次要
    消灭的那类假红。反过来放行而真没装时，bash 自己会报 not recognized，脚本一样跑不成但错误
    可见可查。两种失败都是「脚本没跑成」，所以取「不会挡掉能干活的设备」的那一侧。
    （用户同口径：卸载后不必立刻判红，「执行报错就好」。）
    """
    ent = fresh_entry(device.id)
    rt = ent.get("runtimes") if ent else None
    if rt:
        hit = next((r for r in rt if r.get("name") == "bash"), None)
        if hit is not None:
            return bool(hit.get("installed"))
    try:
        code, out = await exec_command(device, REMOTE_PROBE_CMDS_WIN["bash"], timeout=10)
    except Exception:
        return True                     # 没探到 ≠ 没装：不写缓存（下次还会再探），放行
    first = out.strip().splitlines()[0] if out.strip() else ""
    if not first:
        # 无输出 = 无结论（exec_command 内部超时/半关闭会收敛成 code=-1 + 空输出）。
        # `_installed_from_out("", *)` 会给 False —— 那正是「网络抖动变成没装」的入口，别走。
        return True
    ok = _installed_from_out(first, code)
    _write_bash_runtime(device.id, ok, first)
    return ok


def _write_bash_runtime(device_id: int, installed: bool, version: str) -> None:
    """把 bash 结论**合并**进 probe 缓存的 runtimes（不是整条替换）。

    同一次 remote_probe 的 python/node/powershell 条目还在数组里，整条覆盖会让本机信息页
    只剩 bash 一行；所以未过期时在旧数组上换掉/追加 bash 一条，已过期时只写这一条
    （旧结论不续命，与 cache_probe 的 TTL 语义一致）。
    """
    ent = fresh_entry(device_id)
    old = (ent or {}).get("runtimes") or []
    item = {"name": "bash", "installed": installed, "version": version}
    merged = [r for r in old if r.get("name") != "bash"] + [item]
    cache_probe(device_id, ok=True, runtimes=merged)


def _installed_from_out(first: str, code: int) -> bool:
    """从探测输出判断运行时是否安装（识别 Windows/Linux 的 not found 变体）"""
    if not first or code != 0:
        return False
    low = first.lower()
    nf = ("command not found" in low or "no such file" in low
          or "not recognized" in low or "不是内部或外部命令" in low)
    return not nf


async def remote_probe(device: Device) -> dict:
    """在远端探测平台与运行时版本，返回 envcheck 兼容的结构。

    两段式：首探平台（win32/unix）→ 按平台分发探测命令，避免跨平台误报。
    """
    # 第一段：探测平台（固定 ASCII 标记，避免语言/编码乱码干扰）
    is_win = False
    os_info = ""
    probe_err: str | None = None
    try:
        code, out = await exec_command(device, _PLATFORM_PROBE, timeout=10)
        is_win = "WIN" in out.upper()
    except Exception as e:
        # G1-A：连不上/认证失败必须带出来。此前吞掉异常 → 调用方拿到一个「长得像正常结果」
        # 的 payload（os_info 变成「探测失败: …」），本机信息页无法判定失败、也提示不出原因。
        probe_err = str(e)
        code = 1

    if is_win:
        # win：os 用 ASCII 的 %OS%（Windows_NT），避免 ver 输出 GBK 乱码
        try:
            _, o = await exec_command(device, "echo %OS%", timeout=10)
            os_info = o.strip().splitlines()[0] if o.strip() else "Windows"
        except Exception:
            os_info = "Windows"
        result = {"platform": "win32", "os_info": os_info, "runtimes": []}
        cmds = REMOTE_PROBE_CMDS_WIN
        order = ("python", "node", "powershell", "bash")  # os 已由 %OS% 取得，不再重探 ver（避免 GBK 乱码）
    else:
        result = {"platform": "unix", "os_info": os_info, "runtimes": []}
        cmds = REMOTE_PROBE_CMDS_UNIX
        order = ("uname", "python", "python3", "node", "bash")

    # 第二段：按平台分发探测
    for name in order:
        cmd = cmds[name]
        try:
            c, o = await exec_command(device, cmd, timeout=10)
            first = o.strip().splitlines()[0] if o.strip() else ""
        except Exception as e:
            first = f"探测失败: {e}"
            c = 1
        if name == "uname":
            low = first.lower()
            result["platform"] = "windows" if "microsoft" in low or "windows" in low else "unix"
            result["os_info"] = first
        else:
            result["runtimes"].append(
                {"name": name, "installed": _installed_from_out(first, c), "version": first}
            )
    # G1-A：把「首探就失败」显式化（调用方据此报错；成功时 ok=True，行为与旧版一致）
    result["ok"] = probe_err is None
    result["error"] = probe_err
    return result