import asyncio
import codecs
import locale
import subprocess
import signal
import os
import sys
import json
from datetime import datetime
from typing import Optional, Dict, Any
from pathlib import Path
from ..models.run_history import RunHistory
from ..models.script import Script
from ..models.device import Device
from ..database import async_session
from sqlalchemy import select
import logging

from .win_runtime import resolve_python

logger = logging.getLogger(__name__)

# 输出日志目录（单一来源：config.DATA_DIR，支持 SCRIPTHUB_DATA_DIR 覆盖）
from ..config import DATA_DIR
RUNS_DIR = DATA_DIR / "runs"

# DB 里保存的执行输出：头部 200 行 + 尾部 800 行（批次 BM ⑦/N3）。
# 此前只留尾部 1000 行：2000 行输出的**首部丢失**，「首行」（`$ <命令>` 与脚本开头的报错）永远看不到。
# 日志文件仍是完整输出（_write_log 逐行追加，不受此限）。
DB_HEAD_LINES = 200
DB_TAIL_LINES = 800


def _db_output(full: str) -> str:
    """超长输出 → 头部 200 行 + 省略标记 + 尾部 800 行；未超长原样返回。"""
    lines = full.split("\n")
    if len(lines) <= DB_HEAD_LINES + DB_TAIL_LINES + 1:
        return full
    omitted = len(lines) - DB_HEAD_LINES - DB_TAIL_LINES
    return "\n".join(
        lines[:DB_HEAD_LINES] + [f"... [省略 {omitted} 行] ..."] + lines[-DB_TAIL_LINES:]
    )


def _win_abs(path: str) -> str:
    """Windows cmd 绝对路径：script_hub/...（SFTP 相对用户主目录）→ %USERPROFILE%\\script_hub\\..."""
    p = path.replace("/", "\\").lstrip("\\")
    if p.startswith("script_hub\\"):
        return "%USERPROFILE%\\" + p
    return p


# V5-G（SPEC §2.4）：Shell 覆盖白名单——值 = 该平台上执行「脚本路径 + 参数」的运行器前缀。
# 前端下拉按目标平台出候选（SHELL_BY_TYPE），此处是唯一权威校验点（非法/平台不符 → ValueError）。
SHELL_RUNNERS: Dict[str, Dict[str, str]] = {
    "unix": {"bash": "bash", "sh": "sh", "zsh": "zsh", "python3": "python3"},
    "win32": {
        "bash": "bash",
        "cmd": "cmd /c",
        "powershell": "powershell -NoProfile -ExecutionPolicy Bypass -File",
        "pwsh": "pwsh -NoProfile -File",
    },
}


def resolve_runner(shell: Optional[str], is_win: bool, category: Optional[str] = None) -> Optional[str]:
    """Shell 覆盖 → 运行器前缀；None/空 = 未指定（调用方走 category 默认分派）。

    N6：白名单只保证「这个 shell 在该平台存在」，不保证「这个 shell 能跑这类脚本」——
    此前 `POST /api/run {脚本=hello.bat, shell:"bash"}` 一路放行并执行 `bash hello.bat`。
    给了 category 就必须同族（SHELL_CATEGORIES），跨类型一律 ValueError（路由回 400）。
    """
    if not shell:
        return None
    table = SHELL_RUNNERS["win32" if is_win else "unix"]
    if shell not in table:
        raise ValueError(
            f"Shell「{shell}」不适用于{'Windows' if is_win else 'Unix' } 目标"
            f"（可选：{', '.join(table)}）"
        )
    if category and category not in SHELL_CATEGORIES[shell]:
        allowed = "、".join(sorted(SHELL_CATEGORIES[shell]))
        raise ValueError(
            f"Shell「{shell}」只能给 {allowed} 类脚本用（当前脚本类型：{category}）"
            f"——Shell 覆盖只在同族解释器之间生效，不能跨脚本类型。"
        )
    return table[shell]


# Shell 覆盖的「同族」定义（值 = 允许用该 shell 覆盖的脚本 category）：
# bash/sh/zsh → shell 类；cmd → bat 类；powershell/pwsh → powershell 类；python3 → python 类。
SHELL_CATEGORIES: Dict[str, set] = {
    "bash": {"shell"},
    "sh": {"shell"},
    "zsh": {"shell"},
    "cmd": {"bat"},
    "powershell": {"powershell"},
    "pwsh": {"powershell"},
    "python3": {"python"},
}


# 目标 Windows 的 ANSI 代码页：cmd 的批处理解析器按它读 .bat/.cmd（不是控制台的 UTF-8）。
# 远端不一定是简体中文——繁中 = cp950、西欧 = cp1252，用环境变量覆盖（默认 cp936）。
WIN_ANSI_CODEC_ENV = "SCRIPTHUB_WIN_ANSI_CODEC"


def _win_ansi_codec() -> str:
    """目标 ANSI 代码页（每次读 env，改完不必重启进程）；非法编码名回落 cp936。"""
    codec = os.environ.get(WIN_ANSI_CODEC_ENV) or "cp936"
    try:
        codecs.lookup(codec)
        return codec
    except LookupError:
        logger.warning("%s=%r 不是合法编码名，回落 cp936", WIN_ANSI_CODEC_ENV, codec)
        return "cp936"


def _local_output_codec(category: Optional[str] = None) -> str:
    """本机子进程输出的解码编码（批次 BM ③⑤；BQ ①按命令类别区分）。

    Windows 本机：cmd/PowerShell 经管道回传的不是 UTF-8，而是**控制台代码页**（简中 = cp936）——
    按 UTF-8 解会满屏替换符（U+FFFD），.ps1 的中文变成 `?`。取系统 ANSI 代码页（可用
    环境变量 SCRIPTHUB_WIN_ANSI_CODEC 覆盖，与目标机转码共用同一个旋钮），解码仍带 errors="replace"。
    但 **python/bash 自己发 UTF-8**（Git Bash 一律 UTF-8；python 由启动 env 注入
    PYTHONIOENCODING=utf-8，见 execute_script）——按 GBK 解会把 UTF-8「中文」解成 `涓枂`。
    category=None 维持旧行为（ANSI）；远程路径不经过这里。
    Unix 本机：一律 UTF-8（行为与改动前完全一致）。
    """
    if sys.platform != "win32":
        return "utf-8"
    if category in ("python", "shell"):
        return "utf-8"
    codec = os.environ.get(WIN_ANSI_CODEC_ENV) or locale.getpreferredencoding(False) or "utf-8"
    try:
        codecs.lookup(codec)
        return codec
    except LookupError:
        return _win_ansi_codec()


def _to_crlf(data: bytes) -> bytes:
    """LF / 混合行尾 → 全部 CRLF。

    真机实测（Work Laptop / Win11 26100）：cmd 的批处理解析器在「多字节字符 + 裸 LF 行尾」下
    会失步——仓库里 LF 行尾的 cleanup.bat（中文写在 @REM 里）执行输出
    `'M' is not recognized as an internal or external command`、退出码 9009；
    改成 CRLF 后正常。纯 ASCII 的 LF 批处理不受影响，所以这个转换只对 .bat/.cmd 做。
    """
    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def _to_lf(data: bytes) -> bytes:
    """CRLF / 混合行尾 → 全部 LF（Unix 侧 _to_crlf 的镜像，同款单次 replace 写法）。

    真机实测（本机 Linux，2026-09）：CRLF 行尾的 .sh 跑 `bash x.sh` → `语法错误：未预期的
    文件结束符`、exit 2；`./x.sh` → exit 127（内核把 shebang 读成 `#!/bin/bash\\r`，
    即 `bad interpreter: /bin/bash^M`）。根因：bash 不把 `\\r` 当行尾而当一个普通字符，
    `…; then\\r` 这类词法全被污染。

    只合并 `\\r\\n` 这一个组合，**不删裸 `\\r`**：`\\r` 可以是脚本自己用的控制字符
    （`printf 'a\\rb'` 进度条覆盖），全量删会改坏内容；`\\r` 单独成行只存在于老式 Mac 行尾，
    这里没有这种场景。幂等：已是 LF 的内容原样返回。
    """
    return data.replace(b"\r\n", b"\n")


# Unix 侧做行尾规整的后缀 = shell 家族（CRLF 会让 bash/zsh 的解析直接失败）。
# 不扩到 .py：Python 词法器容忍 \r\n，且这里是 `python3 x.py` 而非内核直执，改了白改。
# 更不扩到通用后缀：依赖清单里可能有二进制/压缩包，动行尾就是写坏文件。
SHELL_SUFFIXES = (".sh", ".bash", ".zsh", ".ksh")


def encode_script_bytes(data: bytes, is_win: bool, name: str) -> tuple[bytes, Optional[str]]:
    """按目标平台与脚本后缀调整上传字节，返回 (payload, 警告文本|None)。

    Unix 目标：**只规整行尾，绝不碰编码**（bash 按 UTF-8 读脚本，转码只会帮倒忙）。
    shell 家族后缀（.sh/.bash/.zsh/.ksh）与「无后缀但以 `#!` 开头」的随传依赖走 _to_lf，
    CRLF → LF；其余（.py 由 `python -X utf8` 负责、.json/二进制依赖等）原样返回。

    Windows 目标：
    - .bat / .cmd → **系统 ANSI 代码页**（默认 cp936，见 _win_ansi_codec）+ 行尾 CRLF。
      实测：UTF-8 原样上传时 cmd 按 ANSI 解析，脚本里的中文路径/文件名落盘即乱码
      （`mkdir "中文目录"` 造出的是 `涓枃鐩綍`）；换成 GBK 后磁盘上就是 `中文目录`。
    - .ps1 → **UTF-8 with BOM**。实测 PS 5.1 无 BOM 时按 ANSI 读脚本：字面量 "中文测试".Length
      读到 6 而不是 4，脚本创建的 `ps中文文件.txt` 实际落盘为乱码名；加 BOM 后长度 4、文件名正确。
      PS 7+ 无 BOM 也能读，BOM 对两个版本都安全，所以统一加（已带 BOM 则不重复加）。

    ANSI 编不下来的字符（GBK 没有的 emoji/生僻字）**不静默丢**：保留 UTF-8 原文字节并返回
    显式警告，由调用方写进运行日志/输出——宁可让用户看到告警，也不写出一个坏文件。
    """
    suffix = Path(name).suffix.lower()
    if not is_win:
        # Unix 侧只规整行尾，编码一律不碰；裸 \r 保留（见 _to_lf）
        if suffix in SHELL_SUFFIXES or data.startswith(b"#!"):
            return _to_lf(data), None
        return data, None
    if suffix in (".bat", ".cmd"):
        if data.startswith(codecs.BOM_UTF8):
            # cmd 不认 BOM：留着会被当成第一行命令的一部分（实测报错形如 '锘?@echo' 不是内部命令）
            data = data[len(codecs.BOM_UTF8):]
        payload = _to_crlf(data)
        codec = _win_ansi_codec()
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return payload, (
                f"[警告] {name} 不是合法 UTF-8，无法转成目标 ANSI({codec})，已原样上传——"
                f"cmd 仍会按 ANSI 解析，中文可能乱码。")
        try:
            return text.encode(codec), None
        except UnicodeEncodeError as e:
            return payload, (
                f"[警告] {name} 含 {codec} 无法表示的字符（第 {e.start + 1} 个字符处），"
                f"已按 UTF-8 原样上传（行尾已转 CRLF）——Windows 端中文/路径可能显示异常。"
                f"目标机代码页不是简体中文时，请用环境变量 {WIN_ANSI_CODEC_ENV}=cp950/cp1252 指定。")
    if suffix == ".ps1" and not data.startswith(codecs.BOM_UTF8):
        return codecs.BOM_UTF8 + data, None
    return data, None


def put_script_encoded(sftp, local_path, remote_path: str, is_win: bool) -> Optional[str]:
    """读本地脚本 → 按目标平台转码 → 写远端（手动执行 / 定时下放共用这一条路，勿复制转码逻辑）。

    返回警告文本（无警告 = None），调用方负责把它带进运行日志/执行输出。
    """
    with open(local_path, "rb") as f:
        raw = f.read()
    payload, warning = encode_script_bytes(raw, is_win, str(remote_path))
    fh = sftp.open(remote_path, "wb")
    try:
        fh.write(payload)
    finally:
        fh.close()
    if warning:
        logger.warning("远端 %s: %s", remote_path, warning)
    return warning


def win_script_cmd(category: str, remote_script: str, arg_str: str, remote_script_dir: str,
                   has_bash: bool = False, shell: Optional[str] = None) -> str:
    """构造 Windows 远端执行命令（**手动执行与定时下放共用的唯一分派点**）。

    原则（PRD V3 风险表）：
    - .py 用 python（非 py launcher）并加 -X utf8 输出 UTF-8（避免 cmd/chcp 嵌套引号坑）
    - .bat 必须显式 cmd /c（OpenSSH 默认 shell 非 cmd）；路径假定无空格（脚本短名）
    - .bat/.ps1 的**编码在 SFTP 上传时按目标平台转码**（见 encode_script_bytes），
      这里**不再 chcp 65001**：chcp 影响的是批处理解析器读文件用的代码页（实测：
      同一份 UTF-8 .bat 有 chcp 时中文目录名正确、去掉就变乱码；反过来对 ANSI 编码的
      .bat 加 chcp 65001 会把它当 UTF-8 解析而乱码）——编码与 chcp 必须二选一，选编码。
    - .ps1 显式 powershell -ExecutionPolicy Bypass -File
    - .sh 有 bash（Git Bash/MSYS2/WSL）则用 `bash` 执行；无则显式拒绝提示
    - cd 用 cd /d（跨盘符）

    定时下放（sched_delegate.SchtasksManager）复用本函数的输出构造 schtasks /TR——两处各写一套
    「扩展名 → 解释器」映射就是下一个 bug（曾经的 `.bat` 被拼成 `python xxx.bat`）。
    """
    if category == "shell":
        if has_bash:
            # Git Bash 执行 .sh；脚本由 SFTP 上传保持本机 LF（Git Bash 接受 LF）
            rs = _win_abs(remote_script)
            rdir = _win_abs(remote_script_dir)
            return (f"cmd /c \"chcp 65001 >nul && cd /d {rdir} "
                    f"&& bash {rs}{' ' + arg_str if arg_str else ''}\"")
        # 无 bash（未装 Git Bash/MSYS2/WSL）→ 明确拒绝 + 提示
        return ("cmd /c \"echo [边界] .sh 脚本在 Windows 目标下不可执行(未检测到 Bash)。"
                "请安装 Git Bash/MSYS2/WSL，或改用 .bat/.ps1/.py。 & exit /b 1\"")
    rs = _win_abs(remote_script)
    rdir = _win_abs(remote_script_dir)
    # V5-G（SPEC §2.4）：显式 Shell 覆盖优先于 category 默认分派
    if shell:
        runner = resolve_runner(shell, True, category)
        if shell == "bash" and not has_bash:
            return ("cmd /c \"echo [边界] 指定用 bash 执行，但目标 Windows 未检测到 Bash。"
                    "请安装 Git Bash/MSYS2/WSL，或改用 cmd/powershell。 & exit /b 1\"")
        inner = f"{runner} \"{rs}\"{' ' + arg_str if arg_str else ''}"
        # chcp 65001 只留给 bash 分支（.sh 一律保持 UTF-8 上传，chcp 仅影响控制台回显）；
        # cmd/powershell/pwsh/python 都按文件自身编码读脚本，65001 会让 ANSI 编码的 .bat 乱码
        pre = "chcp 65001 >nul && " if shell == "bash" else ""
        return f"cmd /c \"{pre}cd /d {rdir} && {inner}\""
    if category == "bat":
        inner = f"call \"{rs}\"{' ' + arg_str if arg_str else ''}"
        return f"cmd /c \"cd /d {rdir} && {inner}\""
    if category == "powershell":
        ps = f"powershell -NoProfile -ExecutionPolicy Bypass -File \"{rs}\"{' ' + arg_str if arg_str else ''}"
        return f"cmd /c \"cd /d {rdir} && {ps}\""
    # python 及其他 → 直接 python（-X utf8 输出 UTF-8，防中文乱码）
    py = f"python -X utf8 \"{rs}\"{' ' + arg_str if arg_str else ''}"
    return f"cmd /c \"cd /d {rdir} && {py}\""


# Unix 目标的 category 默认分派（手动执行与 crontab 下放共用的唯一映射；shell 覆盖优先）
UNIX_CATEGORY_RUNNERS = {"python": "python3", "shell": "bash", "powershell": "pwsh", "bat": "bash"}


def unix_script_cmd(category: str, remote_script: str, arg_str: str = "",
                    shell: Optional[str] = None) -> str:
    """Unix 目标：运行器 + 脚本路径 + 参数（**手动执行与 crontab 下放共用的唯一分派点**）。

    cron 直接执行脚本文件路径会假设「脚本自己可执行」（+x / shebang）——.sh/.py 没有时
    cron 静默失败，与 Windows 侧 `.bat` 被拼成 `python xxx.bat` 是同一类事故。
    """
    runner = resolve_runner(shell, False, category) or UNIX_CATEGORY_RUNNERS.get(category, "bash")
    return f"{runner} {remote_script}{' ' + arg_str if arg_str else ''}"


def _quote(value: str) -> str:
    """参数/路径统一引号包裹（批次 BM ④/N4）。

    路径一律加（`cmd /c "C:\\a\\b.bat"` 合法），参数值也一律加：含空格的会在第一个空格处截断，
    不含空格的加了也无害（`-n "5"`），而"只在需要时加引号"会留下第二种写法与 shell 注入面
    （参数值来自前端输入，属于信任边界）。参数**名**不加——`--count`/`%1`/`-n` 是固定字面量。
    """
    return f'"{value}"'


def _norm_param_key(key: str) -> str:
    return key.lstrip("-")


def normalize_parameters(script: Script, parameters: Dict[str, Any]) -> Dict[str, Any]:
    """裸键归一化（BQ②）：key.lstrip('-') 后与元数据参数名匹配，匹配到改写为带 `--` 的规范键。

    元数据没有 `--count`？只有 `count`：幂等不改写（脚本自己的 argparse 可能定义裸键）。
    匹配不到任何元数据参数 → ValueError（路由回 400「未知参数键」）。
    元数据不是合法 JSON/为空 → 原样返回（不凭空拒绝，与 validate_parameters 同一立场）。
    """
    try:
        defs = json.loads(script.parameters or "[]")
    except (ValueError, TypeError):
        defs = []
    meta = {_norm_param_key(d["name"]): d["name"] for d in defs
            if isinstance(d, dict) and isinstance(d.get("name"), str)}
    if not meta:
        return parameters or {}
    out: Dict[str, Any] = {}
    for key, value in (parameters or {}).items():
        canonical = meta.get(_norm_param_key(key))
        if canonical is None:
            raise ValueError(f"未知参数键「{key}」（脚本元数据里没有这个参数）")
        out[canonical] = value
    return out


def validate_parameters(script: Script, parameters: Dict[str, Any]) -> None:
    """按脚本元数据（script.parameters，parser 产物）校验入参——非法 → ValueError（路由回 400）。

    批次 BM ⑦/N1：此前 `parameters={"count": "abc"}` 被 200 接受，脚本跑到 argparse 才炸（且错误
    只出现在输出里，前端表单不报）。只校验**元数据里已知的参数名**：脚本运行期还可能吃元数据没解析
    出来的参数，不该由这里凭空拒绝。类型按 parser 的命名（string/int/float/bool）。
    """
    try:
        defs = json.loads(script.parameters or "[]")
    except (ValueError, TypeError):
        defs = []   # 元数据本身不是合法 JSON：不拦（不是本次入参的问题），别把既有坏数据变成 500
    types = {_norm_param_key(d["name"]): d.get("type") for d in defs
             if isinstance(d, dict) and isinstance(d.get("name"), str)}

    for name, value in (parameters or {}).items():
        if value is None or isinstance(value, bool):
            continue
        if not isinstance(value, (int, float, str)):
            raise ValueError(
                f"参数「{name}」的值类型不支持（{type(value).__name__}），只接受字符串/数字/开关")
        want = types.get(_norm_param_key(name))
        try:
            if want == "int":
                int(str(value).strip())        # "5" 合法（HTML input 送的就是字符串）、"5.5"/"abc" 非法
            elif want == "float":
                float(str(value).strip())
        except ValueError:
            raise ValueError(f"参数「{name}」需要{'整数' if want == 'int' else '数字'}，收到 {value!r}")


class ScriptExecutor:
    """脚本执行引擎"""
    
    def __init__(self):
        self.running_processes: Dict[int, asyncio.subprocess.Process] = {}
        self.running_remote: Dict[int, asyncio.Event] = {}  # 远程执行的取消事件
        # 本机执行的取消标志（与远端 cancel_event 对称：kill_process 置位 → 执行体据此落 killed；
        # 不能用 returncode==-15 判 killed，脚本自己 kill -TERM $$ 也是 -15）
        self.local_cancelled: set[int] = set()
        # 确保日志目录存在（用当前用户权限）
        try:
            RUNS_DIR.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            logger.warning(f"无法创建日志目录 {RUNS_DIR}，日志文件功能将不可用")
    
    def _write_log(self, output_file: Optional[Path], content: str, mode: str = 'a'):
        """写入日志文件，失败时静默降级"""
        if not output_file:
            return False
        try:
            with open(output_file, mode, encoding='utf-8') as f:
                f.write(content)
            return True
        except (PermissionError, OSError) as e:
            logger.warning(f"写入日志文件失败: {e}")
            return False
    
    async def _update_db(self, run_history_id: int, **kwargs):
        """更新运行记录的指定字段"""
        try:
            async with async_session() as db:
                result = await db.execute(select(RunHistory).where(RunHistory.id == run_history_id))
                rh = result.scalar_one_or_none()
                if rh:
                    for k, v in kwargs.items():
                        setattr(rh, k, v)
                    await db.commit()
        except Exception as e:
            logger.error(f"更新DB失败: {e}")

    async def execute_script(
        self,
        script: Script,
        run_history_id: int,
        parameters: Dict[str, Any],
        working_dir: Optional[str] = None,
        env_vars: Optional[Dict[str, str]] = None,
        timeout: int = 0,
        shell: Optional[str] = None,
    ):
        """执行脚本（后台任务，不阻塞API）"""
        collected_output = ""
        exit_code = -1
        status = "failed"
        output_file = None
        
        # 构建命令
        command = self._build_command(script, parameters, shell=shell)
        
        # 准备环境变量
        env = os.environ.copy()
        if env_vars:
            env.update(env_vars)
        # BQ①：管道下 python 的 stdout 编码不定（老版本/Windows 按 locale），显式钉成 UTF-8，
        # 与 _local_output_codec("python") 的 utf-8 解码配套；Unix 下注入同样无害
        if script.category == "python":
            env["PYTHONIOENCODING"] = "utf-8"
        
        # 确定工作目录
        cwd = working_dir or script.working_dir or str(Path(script.path).parent)
        
        # 尝试创建输出日志文件
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = RUNS_DIR / f"run_{run_history_id}_{timestamp}.log"
        
        # 测试文件是否可写
        file_writable = self._write_log(output_file, "", mode='w')
        if not file_writable:
            output_file = None  # 降级为纯DB模式
        
        # 更新命令和输出文件路径到数据库
        await self._update_db(run_history_id, command=command, output_file=str(output_file) if output_file else None)
        
        try:
            # 启动进程
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
                start_new_session=True
            )
            
            # 保存进程引用
            self.running_processes[run_history_id] = process
            
            # 实时读取输出并写入DB和文件
            collected_output = f"$ {command}\n\n"
            await self._update_db(run_history_id, output=collected_output)
            self._write_log(output_file, collected_output, mode='w')
            
            # 并行读取 stdout 和 stderr
            out_codec = _local_output_codec(script.category)   # Windows 本机：cmd/PS=ANSI，python/bash=UTF-8
            async def read_stream(stream):
                nonlocal collected_output
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    text = line.decode(out_codec, errors='replace')
                    collected_output += text
                    
                    # 写入文件（降级模式下跳过）
                    self._write_log(output_file, text)
                    
                    # DB 存头部 200 行 + 尾部 800 行（ponytail: 高频写，后续可改为批量/节流写）
                    await self._update_db(run_history_id, output=_db_output(collected_output))
            
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        read_stream(process.stdout),
                        read_stream(process.stderr)
                    ),
                    timeout=timeout if timeout > 0 else None
                )
                await process.wait()

                if run_history_id in self.local_cancelled:
                    # 用户点了「终止」：标志优先于退出码（被 SIGTERM 杀 → returncode=-15，不能当 failed）
                    exit_code = -1
                    status = "killed"
                    kill_msg = "\n[已终止] 本地执行被终止"
                    collected_output += kill_msg
                    self._write_log(output_file, kill_msg)
                else:
                    exit_code = process.returncode if process.returncode is not None else 0
                    status = "success" if exit_code == 0 else "failed"
                
            except asyncio.TimeoutError:
                await self._kill_process_tree(process)
                timeout_msg = f"\n[超时] 脚本执行超过 {timeout} 秒"
                collected_output += timeout_msg
                self._write_log(output_file, timeout_msg)
                exit_code = -1
                status = "timeout"
            except asyncio.CancelledError:
                cancel_msg = "\n[取消] 脚本被取消"
                collected_output += cancel_msg
                self._write_log(output_file, cancel_msg)
                exit_code = -1
                status = "killed"
                
        except Exception as e:
            logger.error(f"执行脚本失败: {e}")
            error_msg = f"\n执行失败: {str(e)}"
            collected_output += error_msg
            self._write_log(output_file, error_msg)
            exit_code = -1
            status = "failed"
            
        finally:
            self.running_processes.pop(run_history_id, None)
            self.local_cancelled.discard(run_history_id)
        
        # 最终写回（DB 存头部 200 行 + 尾部 800 行，见 _db_output）
        db_output = _db_output(collected_output)

        finished_at = datetime.now()
        duration = None
        started = await self._get_field(run_history_id, "started_at")
        if started:
            duration = (finished_at - started).total_seconds()

        await self._update_db(
            run_history_id,
            output=db_output,
            exit_code=exit_code,
            status=status,
            duration=duration,
            finished_at=finished_at
        )

    async def _get_field(self, run_history_id: int, field: str):
        """读取运行记录的某字段"""
        try:
            async with async_session() as db:
                result = await db.execute(select(RunHistory).where(RunHistory.id == run_history_id))
                rh = result.scalar_one_or_none()
                if rh:
                    return getattr(rh, field)
        except Exception as e:
            logger.error(f"读取DB失败: {e}")
        return None
    
    async def kill_process(self, run_id: int) -> bool:
        """终止运行中的进程（本机进程树 / 远程通道）"""
        # 远程执行：置位取消事件 → exec_command 关闭 channel 终止远端进程
        remote_event = self.running_remote.get(run_id)
        if remote_event:
            remote_event.set()
            return True
        process = self.running_processes.get(run_id)
        if process:
            self.local_cancelled.add(run_id)  # 先置位再杀：执行体据此落 killed（对称远端 event.set()）
            await self._kill_process_tree(process)
            return True
        return False
    
    async def _kill_process_tree(self, process: asyncio.subprocess.Process):
        """终止进程树"""
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                    capture_output=True
                )
            else:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass
            
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                
        except Exception as e:
            logger.error(f"终止进程失败: {e}")
    
    def _build_command(self, script: Script, parameters: Dict[str, Any],
                       shell: Optional[str] = None) -> str:
        """构建执行命令（本机）。shell 指定时覆盖 category 默认解释器（SPEC §2.4）。

        N4：路径/含空格的参数值**统一引号包裹**——`cmd /c C:\\脚本 目录\\中文 测试.bat` 会在第一个
        空格处截断（被当成「命令 + 参数」），中文路径尤其容易撞上；powershell -File 同理。
        """
        path = _quote(script.path)
        runner = resolve_runner(shell, sys.platform == "win32", script.category)
        if runner:
            cmd = f"{runner} {path}"
        elif script.category == "python":
            # 本机 Python：resolve_python() 实测过滤 Windows 的 python3 Store 假垫片
            # （跑起来只打印商店提示、exit 9009 → 本机 .py 全失败）；全假回退 sys.executable
            cmd = f"{_quote(resolve_python())} {path}"
        elif script.category == "shell":
            cmd = f"bash {path}"
        elif script.category == "bat":
            cmd = f"cmd /c {path}"
        elif script.category == "powershell":
            cmd = f"powershell -ExecutionPolicy Bypass -File {path}"
        else:
            cmd = path

        args = []
        for param_name, param_value in parameters.items():
            if isinstance(param_value, bool):
                if param_value:
                    args.append(f"--{param_name.lstrip('-')}" if param_name.lstrip('-') else param_name)
            elif param_value not in (None, ''):
                args.append(f"--{param_name.lstrip('-')} {_quote(str(param_value))}")

        if args:
            cmd += " " + " ".join(args)

        return cmd

    async def execute_remote(
        self,
        script: Script,
        device: Device,
        run_history_id: int,
        parameters: Dict[str, Any],
        timeout: int = 0,
        shell: Optional[str] = None,
    ):
        """远程执行脚本：SFTP 上传到远端 /tmp/script_hub/ 后执行，输出逐行回传 DB。

        PRD 设计要点：脚本传输 SFTP → 远端 /tmp/script_hub/；输出写 DB（WS 轮询等价推送）。
        复用本机 _update_db / 日志 / 超时 / 状态写回。
        """
        from ..services.ssh_service import pool, exec_command

        collected_output = ""
        exit_code = -1
        status = "failed"
        output_file = None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = RUNS_DIR / f"run_{run_history_id}_{timestamp}.log"
        if not self._write_log(output_file, "", mode='w'):
            output_file = None

        # 远端临时目录（按脚本隔离，保留相对目录结构）
        remote_dir = f"/tmp/script_hub/{script.id}"
        remote_script = f"{remote_dir}/{script.relative_path}"

        # 解析依赖清单（相对脚本根路径）
        dependencies = []
        if script.dependencies:
            try:
                dependencies = json.loads(script.dependencies) or []
            except Exception:
                dependencies = []

        cancel_event = asyncio.Event()
        self.running_remote[run_history_id] = cancel_event

        remote_cmd_display = f"ssh {device.username}@{device.host}:{device.port} 执行 {script.name}"
        await self._update_db(run_history_id, command=remote_cmd_display, output_file=str(output_file) if output_file else None)

        collected_output = f"$ {remote_cmd_display}\n\n"
        await self._update_db(run_history_id, output=collected_output)
        self._write_log(output_file, collected_output, mode='w')

        # 脚本根目录（解析依赖源文件 + 供失败提示缺失文件）
        from ..config import get_script_root
        script_root = get_script_root()
        missing_local = [d for d in dependencies if not (script_root / d).exists()]

        if missing_local:
            collected_output += f"\n[警告] 以下随传文件在本机不存在，跳过上传: {', '.join(missing_local)}\n"
            await self._update_db(run_history_id, output=collected_output)
            self._write_log(output_file, f"\n[警告] 以下随传文件在本机不存在，跳过上传: {', '.join(missing_local)}\n")

        try:
            client = await pool.get(device)
            # 探测远端平台（win32/unix），决定命令构造/目录/清理/编码
            from ..services.ssh_service import is_win_device
            is_win = await is_win_device(device)
            if is_win:
                # Windows：SFTP 传相对路径（无前导 / → OpenSSH sftp 落用户主目录）；cmd 用 %USERPROFILE% 定位
                # (ponytail: 避免 C:/Windows/Temp 权限问题；强依赖 %USERPROFILE% 环境变量)
                remote_dir = f"script_hub/{script.id}"  # SFTP 相对用户主目录
            else:
                remote_dir = f"/tmp/script_hub/{script.id}"
            # remote_script：sftp 用相对；win cmd 由 _build_win_cmd 加 %USERPROFILE% 前缀
            remote_script_raw = f"{remote_dir}/{script.relative_path}"
            remote_script = remote_script_raw
            remote_script_dir = remote_script.rsplit("/", 1)[0]

            async def _cb(text):
                nonlocal collected_output
                collected_output += text
                self._write_log(output_file, text)
                await self._update_db(run_history_id, output=_db_output(collected_output))

            # SFTP 上传主脚本 + 依赖（保留相对目录结构；目录递归创建，兼容 win 相对 home 路径）
            # 按目标平台转码（Windows: .bat/.cmd→ANSI+CRLF、.ps1→UTF-8 BOM；
            # Unix: shell 脚本 CRLF→LF，其余原样）
            enc_warnings: list[str] = []

            def _upload():
                sftp = client.open_sftp()

                def _mkdirs(path: str):
                    """递归建 SFTP 目录（paramiko mkdir 不递归；保留绝对路径前导 /）"""
                    cur = "/" if path.startswith("/") else ""
                    for part in path.split("/"):
                        if not part:
                            continue
                        cur = f"{cur}/{part}" if cur else part
                        try:
                            sftp.mkdir(cur)
                        except OSError:
                            pass
                    return path

                _mkdirs(remote_dir)
                # 递归建远端相对子目录
                def _mkpath(rel: str):
                    parts = rel.split("/")
                    cur = remote_dir
                    for p in parts[:-1]:
                        cur = f"{cur}/{p}"
                        try:
                            sftp.mkdir(cur)
                        except OSError:
                            pass

                # 上传主脚本
                _mkpath(script.relative_path)
                warn = put_script_encoded(sftp, script.path, remote_script, is_win)
                if warn:
                    enc_warnings.append(warn)

                # 上传依赖清单文件
                imported_deps = []
                for dep in dependencies:
                    src = script_root / dep
                    if not src.exists():
                        continue
                    dst = f"{remote_dir}/{dep}"
                    _mkpath(dep)
                    warn = put_script_encoded(sftp, str(src), dst, is_win)
                    if warn:
                        enc_warnings.append(warn)
                    imported_deps.append(dep)
                sftp.close()
                return imported_deps
            imported_deps = await asyncio.to_thread(_upload)

            if enc_warnings:
                # 转码告警必须让用户看见（GBK 表达不了的字符等）：写日志 + 进执行输出
                warn_text = "".join(f"{w}\n" for w in enc_warnings)
                collected_output += warn_text
                self._write_log(output_file, warn_text)
                await self._update_db(run_history_id, output=collected_output)

            if imported_deps:
                dep_line = ", ".join(imported_deps)
                collected_output += f"[随传文件] {dep_line}\n"
                await self._update_db(run_history_id, output=collected_output)
                self._write_log(output_file, f"[随传文件] {dep_line}\n")

            # 组装远端执行命令（cd 至远端脚本目录，脚本按相对路径引用依赖）
            args = []
            for pn, pv in parameters.items():
                if isinstance(pv, bool):
                    if pv:
                        args.append(pn)
                elif pv not in (None, ''):
                    args.append(f"{pn} {pv}")
            arg_str = " ".join(args)
            remote_script_dir = remote_script.rsplit("/", 1)[0]

            if is_win:
                # Windows 跑 .sh 需探测远端 bash（Git Bash/MSYS2/WSL）
                has_bash = False
                if script.category == "shell":
                    from ..services.ssh_service import detect_has_bash
                    has_bash = await detect_has_bash(device)
                full_cmd = win_script_cmd(script.category, remote_script, arg_str, remote_script_dir,
                                          has_bash=has_bash, shell=shell)
            else:
                # Unix 分支（既有逻辑）
                remote_cmd = unix_script_cmd(script.category, remote_script, arg_str, shell)
                full_cmd = f"mkdir -p {remote_script_dir} && cd {remote_script_dir} && {remote_cmd}"

            # 执行（输出实时回调写 DB），拿到退出码；cd 至脚本所在目录使相对引用依赖正确
            code, _ = await exec_command(
                device,
                full_cmd,
                timeout=timeout or 300,
                output_cb=_cb,
                cancel_event=cancel_event,
            )
            if cancel_event.is_set():
                exit_code = -1
                status = "killed"
                collected_output += "\n[已终止] 远程执行被终止"
                self._write_log(output_file, "\n[已终止] 远程执行被终止")
            else:
                exit_code = code
                status = "success" if code == 0 else "failed"
                # 缺失检测：即使退出码为 0，若输出含"文件不存在/命令未找到"迹象，提示可能缺随传文件
                # （bash 中 source 缺失常不导致整体非零退出，故需检查输出）
                if "No such file" in collected_output or "command not found" in collected_output \
                        or "没有那个文件或目录" in collected_output or "未找到命令" in collected_output:
                    hint = "\n[提示] 输出提示缺少文件或命令。若脚本 source/import 了本地文件，请将其加入脚本的「随传文件」清单后重试。"
                    status_hint = True
                else:
                    hint = None
                    status_hint = False
                if code != 0 or status_hint:
                    collected_output += hint or ("\n[提示] 执行失败。请检查随传文件是否完整（本机文件缺失可能导致远端引用失败）。")
                    self._write_log(output_file, collected_output.rsplit("\n", 1)[-1])
                    await self._update_db(run_history_id, output=collected_output)

            # 清理远端脚本目录（主脚本 + 依赖）
            def _cleanup():
                try:
                    if is_win:
                        client.exec_command(
                            f'cmd /c "rmdir /s /q %USERPROFILE%\\{remote_script_dir.replace("/", "\\\\")} 2>nul"',
                            timeout=10)
                    else:
                        client.exec_command(f"rm -rf {remote_dir}", timeout=10)
                except Exception:
                    pass
            await asyncio.to_thread(_cleanup)
        except asyncio.TimeoutError:
            exit_code = -1
            status = "timeout"
            collected_output += f"\n[超时] 远程执行超过 {timeout} 秒"
            self._write_log(output_file, f"\n[超时] 远程执行超过 {timeout} 秒")
        except Exception as e:
            logger.error(f"远程执行失败: {e}")
            err = f"\n远程执行失败: {str(e)}"
            collected_output += err
            self._write_log(output_file, err)
            exit_code = -1
            status = "failed"
        finally:
            # 无论成功失败，清理取消事件
            self.running_remote.pop(run_history_id, None)

        db_output = _db_output(collected_output)
        finished_at = datetime.now()
        started = await self._get_field(run_history_id, "started_at")
        duration = (finished_at - started).total_seconds() if started else None
        await self._update_db(
            run_history_id,
            output=db_output,
            exit_code=exit_code,
            status=status,
            duration=duration,
            finished_at=finished_at,
        )


# 全局执行器实例
executor = ScriptExecutor()


# 后台执行任务强引用：asyncio 只对 Task 持**弱**引用，`create_task(_run())` 扔完不存引用时，
# 任务可能在跑的中途被 GC 掉（官方文档明示的坑）→ 状态永远停在 running，且没人再去写终态。
# 这里持有引用直到任务结束（done_callback 里丢弃）。
_BACKGROUND_TASKS: set = set()


def spawn_background(coro) -> asyncio.Task:
    """启动后台执行任务并持有强引用（执行完成的状态落库在 executor 内，与本函数无关）。"""
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


async def fail_stale_running(reason: str = "[中断] 服务重启，本次执行未正常结束") -> int:
    """启动时收尾上一次进程留下的 running 记录（返回处理条数）。

    running 只可能由「持有该次执行的进程内的 asyncio 任务」推进到终态；进程一旦重启/崩溃，
    那个任务就没了，记录会永久停在 running —— 与前端是否连着 websocket 无关。启动时统一落终态，
    保证「没有任何前端连接时记录也必须到终态」这个不变量。
    """
    async with async_session() as db:
        rows = (await db.execute(
            select(RunHistory).where(RunHistory.status == "running")
        )).scalars().all()
        if not rows:
            return 0
        now = datetime.now()
        for rh in rows:
            rh.status = "failed"
            rh.exit_code = -1
            rh.finished_at = now
            if rh.started_at:
                rh.duration = (now - rh.started_at).total_seconds()
            rh.output = (rh.output or "") + f"\n{reason}\n"
        await db.commit()
    logger.warning("启动收尾：%d 条 running 记录已置为 failed", len(rows))
    return len(rows)
