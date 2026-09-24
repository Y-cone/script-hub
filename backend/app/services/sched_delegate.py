"""调度下放（PRD-V5 4.J / Phase V5-E）。

把工具内定时任务写入目标设备的系统调度器（Linux crontab 标记块 / Windows schtasks），
任务随设备系统调度器执行——工具完全退出后照跑（方案 1 终态，见 PRD 4.I 决策记录）。

组成：
- 脚本持久部署：`scripthub-deploy/<script_id>/`（远端固定目录 + 依赖随传 + 脏标记增量同步）
- crontab 标记块管理（Linux 远端/本机）：绝不触碰用户已有行
- schtasks 管理（Windows 远端）：cron→/SC 受限映射
- 运行历史拉模式回传：cron.log 增量拉取解析入库（source=device-cron，非实时）
- 漂移对账：远端实际 vs DB 对比，差异标黄 + 一键重写

边界（4.J 不做）：秒级 interval、复杂 cron→schtasks 全量映射、实时推送、多设备同任务。
"""
import json
import logging
from datetime import datetime
from pathlib import PurePosixPath

from ..config import get_script_root
from ..models.schedule import Schedule
from ..models.script import Script
from .executor import unix_script_cmd, win_script_cmd   # 运行器分派的唯一来源（勿在本文件另写映射）

logger = logging.getLogger(__name__)


# crontab 标记块（绝不动用户已有行）
BEGIN_MARK = "# --- scripthub-begin:sched:{sid} ---"
END_MARK = "# --- scripthub-end:sched:{sid} ---"

# 脏标记：脚本更新后待增量同步（内存态；重启后按 mtime/size 比对兜底）
_deploy_dirty: set[int] = set()

# schtasks 支持的 cron 映射（受限）：只支持「每天/每周/一次」形态
import re

_CRON_DAILY = re.compile(r"^(\d+) (\d+) \* \* \*$")          # m h * * *
_CRON_WEEKLY = re.compile(r"^(\d+) (\d+) \* \* ([0-7])$")     # m h * * dow


def mark_dirty(script_id: int):
    """脚本/依赖被编辑 → 标记脏（下次同步时增量更新远端副本）。由脚本保存接口调用。"""
    _deploy_dirty.add(script_id)


def deploy_dir(script_id: int) -> str:
    """远端部署目录（POSIX 风格相对路径；Windows 目标由 SSH 层映射 %USERPROFILE%）。"""
    return f"scripthub-deploy/{script_id}"


def map_cron_to_schtasks(cron: str) -> dict | None:
    """cron 表达式 → schtasks 参数（受限映射）。不支持返回 None（UI 提示改用工具内执行）。"""
    m = _CRON_DAILY.match(cron.strip())
    if m:
        minute, hour = m.group(1), m.group(2)
        return {"sc": "DAILY", "st": f"{int(hour):02d}:{int(minute):02d}"}
    m = _CRON_WEEKLY.match(cron.strip())
    if m:
        minute, hour, dow = m.groups()
        # cron: 0/7=周日 1-6=周一~周六；schtasks MON..SUN
        days = {0: "SUN", 7: "SUN", 1: "MON", 2: "TUE", 3: "WED", 4: "THU", 5: "FRI", 6: "SAT"}
        return {"sc": "WEEKLY", "d": days[int(dow)], "st": f"{int(hour):02d}:{int(minute):02d}"}
    return None


def _schtasks_tr(win_cmd: str, log_file: str) -> str:
    """Windows 执行命令 + 日志重定向 → schtasks /TR 的值。

    /TR 的内层引号按 CRT 约定转义成 `\\"`（外层引号由调用方加，见 SchtasksManager.upsert 里的
    `/TR \\"...\\"`；既有实现就是这个约定，真机上建任务成功过）。`.bat` 分支的命令自带
    `call "..."`，内层引号不转义会被提前吃掉 → 任务建出来执行不了。
    另注：%USERPROFILE% 在本命令经 cmd 下发时就被展开成字面路径存进任务里（既有行为，未改）。
    """
    return win_cmd.replace('"', '\\"') + f" >> {log_file} 2>&1"


async def deploy_script(script: Script, device, client, is_win: bool) -> str:
    """部署脚本+依赖到远端 scripthub-deploy/<id>/。返回远端脚本路径（POSIX 风格，$HOME 前缀）。

    上传字节按目标平台转码（与手动执行同一条路，见 executor.put_script_encoded）：
    Windows 的 .bat/.cmd 转系统 ANSI + CRLF、.ps1 加 UTF-8 BOM；Unix 的 shell 脚本 CRLF→LF，
    其余后缀原样。

    `is_win` **必须**由调用方用实时探测（ssh_service.is_win_device）算好传入，且与选
    crontab/schtasks 的判据是同一个值——否则会出现「按 Windows 转码上传、却注册成 Linux cron」
    这种比现状更糟的组合。不在这里读 device.type（SPEC：该字段仅 UI 区分，可能标错/过期）。
    """
    base = deploy_dir(script.id)
    from ..services.executor import put_script_encoded
    sftp = client.open_sftp()

    def _mkdirp(path: str):
        """递归建远端目录（已存在忽略）。"""
        cur = ""
        for part in path.split("/"):
            cur = f"{cur}/{part}" if cur else part
            try:
                sftp.mkdir(cur)
            except OSError:
                pass

    _mkdirp(base)
    rel_parts = PurePosixPath(script.relative_path).parts
    _mkdirp("/".join([base, *rel_parts[:-1]]))
    remote_script = f"{base}/{script.relative_path}"
    put_script_encoded(sftp, script.path, remote_script, is_win)

    # 依赖随传
    try:
        deps = json.loads(script.dependencies or "[]") or []
    except Exception:
        deps = []
    root = get_script_root()
    for dep in deps:
        src = root / dep
        if not src.exists():
            continue
        _mkdirp(f"{base}/{dep.rsplit('/', 1)[0]}" if "/" in dep else base)
        try:
            put_script_encoded(sftp, str(src), f"{base}/{dep}", is_win)
        except Exception as e:
            logger.warning(f"部署依赖失败 {dep}: {e}")
    sftp.close()
    _deploy_dirty.discard(script.id)

    # 远端脚本绝对路径（执行命令里用 $HOME 前缀；Windows 由 upsert 换 %USERPROFILE%）
    return f"$HOME/{remote_script}"


class CrontabManager:
    """Linux 目标 crontab 标记块管理（crontab -l → 改块 → crontab - 整体写回）。"""

    def __init__(self, device, client):
        self.device = device
        self.client = client

    async def _read(self) -> str:
        from ..services.ssh_service import exec_command
        code, out = await exec_command(self.device, "crontab -l 2>/dev/null || true", timeout=10)
        return out

    async def _write(self, content: str):
        from ..services.ssh_service import exec_command
        # 经 stdin 写入（base64 防 shell 转义坑）
        import base64
        b64 = base64.b64encode(content.encode()).decode()
        cmd = f"echo {b64} | base64 -d | crontab -"
        code, out = await exec_command(self.device, cmd, timeout=10)
        if code != 0:
            raise RuntimeError(f"crontab 写入失败: {out[:200]}")

    def _extract_block(self, content: str, sid: int) -> str | None:
        begin = BEGIN_MARK.format(sid=sid)
        end = END_MARK.format(sid=sid)
        if begin in content and end in content:
            s = content.index(begin)
            e = content.index(end) + len(end)
            return content[s:e]
        return None

    async def upsert(self, sched: Schedule, script: Script, remote_script: str,
                     args_str: str = ""):
        """写入/更新标记块（幂等：先删旧块再插新块）。

        执行行 = `cd 部署目录 && <运行器> <脚本> <参数> >> cron.log 2>&1`：
        - 运行器由 executor.unix_script_cmd 分派（与手动执行同一处映射）——此前直接写脚本文件
          路径，等于假设脚本自带可执行位/shebang；`.sh`/`.py` 没有时 cron 静默失败（同类事故：
          Windows 侧 `.bat` 被拼成 `python xxx.bat`）。
        - `cd` 部署目录：与手动执行一致，脚本里 `source ./随传文件` 这类相对引用才成立。
        """
        content = await self._read()
        sid = sched.id
        # 删旧块（含前后空行规整）
        begin, end = BEGIN_MARK.format(sid=sid), END_MARK.format(sid=sid)
        if begin in content:
            s = content.index(begin)
            e = content.index(end) + len(end)
            content = content[:s] + content[e:]
        base = deploy_dir(script.id)
        log_file = f"$HOME/{base}/cron.log"
        # cron 命令行里的 `%` 被解释成换行（POSIX），参数里的 % 必须转义；脚本路径不含 %，无需处理
        # remote_script 已是 deploy_script 返回的 `$HOME/<部署目录>/<脚本>`（别再拼一次 $HOME）
        cmd = unix_script_cmd(script.category, remote_script, args_str.replace("%", r"\%"))
        line = f"{sched.cron_expr} cd $HOME/{base} && {cmd} >> {log_file} 2>&1"
        block = (
            f"{begin}\n"
            f"{line if sched.enabled else '# DISABLED ' + line}\n"
            f"{end}\n"
        )
        # 追加到末尾（前保一个空行）
        if content and not content.endswith("\n\n"):
            content = content.rstrip("\n") + "\n\n"
        content += block
        await self._write(content)

    async def remove(self, sid: int):
        content = await self._read()
        begin, end = BEGIN_MARK.format(sid=sid), END_MARK.format(sid=sid)
        if begin not in content:
            return
        s = content.index(begin)
        e = content.index(end) + len(end)
        content = content[:s] + content[e:]
        await self._write(content)

    async def audit(self) -> dict[int, str]:
        """漂移对账：解析远端标记块 → {sid: 状态}。status: ok/disabled/missing。"""
        content = await self._read()
        result: dict[int, str] = {}
        for m in re.finditer(r"scripthub-begin:sched:(\d+)", content):
            sid = int(m.group(1))
            block = self._extract_block(content, sid)
            if block is None:
                continue
            result[sid] = "disabled" if "# DISABLED" in block else "ok"
        return result


class SchtasksManager:
    """Windows 目标 schtasks 管理（受限映射，见 map_cron_to_schtasks）。"""

    TASK_NAME = "scripthub-sched-{sid}"

    def __init__(self, device, client):
        self.device = device
        self.client = client

    def _name(self, sid: int) -> str:
        return self.TASK_NAME.format(sid=sid)

    async def upsert(self, sched: Schedule, script: Script, remote_script: str,
                     args_str: str = ""):
        """写入/更新计划任务（幂等：先删旧任务再建）。

        /TR 用 executor.win_script_cmd 构造——**与手动执行同一个分派点**：`cmd /c "cd /d <部署目录>
        && <运行器> <脚本> <参数>"`。此前一律 `python <脚本路径>`（不管扩展名），于是 .bat/.ps1/.sh
        都被 python 当输入文件执行 → 必失败；只有 .py 恰好正确。
        """
        from ..services.ssh_service import exec_command, detect_has_bash
        mapping = map_cron_to_schtasks(sched.cron_expr or "")
        if mapping is None:
            raise ValueError(
                f"cron 表达式 '{sched.cron_expr}' 无法映射为 schtasks（仅支持每天/每周/一次），"
                "请改用本工具内执行")
        base = deploy_dir(script.id).replace("/", chr(92))
        log_file = f"%USERPROFILE%\\{base}\\cron.log"
        # Windows 无 $HOME；exec_command 走 cmd → python 用 %USERPROFILE% 由 cmd 展开
        exe_win = remote_script.replace("$HOME", "%USERPROFILE%")
        # .sh 要在目标机跑得有 Git Bash（探测结果按设备缓存，与手动执行同一函数）
        has_bash = await detect_has_bash(self.device) if script.category == "shell" else False
        win_cmd = win_script_cmd(script.category, exe_win, args_str,
                                 exe_win.rsplit("/", 1)[0], has_bash=has_bash)
        tr = _schtasks_tr(win_cmd, log_file)
        # 先删旧任务（幂等）
        await exec_command(self.device, f'schtasks /Delete /TN "{self._name(sched.id)}" /F >nul 2>&1', timeout=10)
        parts = [f'schtasks /Create /TN "{self._name(sched.id)}"',
                 f"/TR \"{tr}\"", "/SC", mapping["sc"], "/F"]
        if "d" in mapping:
            parts += ["/D", mapping["d"]]
        parts += ["/ST", mapping["st"]]
        # 注意：schtasks /Create 无 /DISABLE 参数（真机实测）——禁用 = 创建后 /Change /DISABLE
        code, out = await exec_command(self.device, " ".join(parts), timeout=15)
        if code != 0:
            raise RuntimeError(f"schtasks 创建失败: {out[:200]}")
        if not sched.enabled:
            code2, out2 = await exec_command(
                self.device, f'schtasks /Change /TN "{self._name(sched.id)}" /DISABLE', timeout=10)
            if code2 != 0:
                raise RuntimeError(f"schtasks 停用失败: {out2[:200]}")

    async def remove(self, sid: int):
        from ..services.ssh_service import exec_command
        await exec_command(self.device, f'schtasks /Delete /TN "{self._name(sid)}" /F >nul 2>&1', timeout=10)

    async def audit(self) -> dict[int, str]:
        from ..services.ssh_service import exec_command
        code, out = await exec_command(self.device, "schtasks /Query /FO LIST 2>nul", timeout=15)
        result = {}
        for m in re.finditer(r"scripthub-sched-(\d+)", out or ""):
            sid = int(m.group(1))
            if sid not in result:
                result[sid] = "ok"
        return result


def manager_for(device, client, is_win: bool):
    """按目标平台返回调度器管理器。

    `is_win` 由调用方用**实时探测**（ssh_service.is_win_device）算好传入——与上传转码
    （deploy_script 的 is_win）必须同一个值，也与手动执行的判据同一来源；不读 device.type
    （SPEC：该字段仅 UI 区分，可能标错/过期）。
    """
    if is_win:
        return SchtasksManager(device, client)
    return CrontabManager(device, client)


async def read_log_since(client, log_path: str, offset: int) -> tuple[int, str]:
    """SFTP 拉取设备端 cron.log 自 offset 起的增量。返回 (新 offset, 增量文本)。

    解码走 RemoteOutputDecoder（批次 AA 的混合解码）：Windows 目标上 cron.log 里是 cmd 按系统
    ANSI(cp936) 写的中文，直接 `decode("utf-8", errors="replace")` 会让历史记录满屏 U+FFFD
    （与 AA 修的执行日志同一个根因，这里是另一个读取点）。Unix 目标的 UTF-8 日志逐字节不变。
    offset 按**读到的原始字节数**推进——旧写法用「解出文本再 encode 的长度」，遇到被替换的字符
    会漂移，重复或漏拉。
    # ponytail: 每次调用都新建解码器 + flush 尾半截，所以日志正在写入时那半行（含被字节边界切开的
    # 中文字符）会先落一个乱码字节、剩余字节在下次增量里单独出现。远端 cron.log 由 >> 追加且读取
    # 紧跟执行之后，命中窗口极小；要根治需把 offset→未完整序列 的尾巴状态按 (设备, 路径) 缓存。
    """
    from ..services.ssh_service import RemoteOutputDecoder
    sftp = client.open_sftp()
    try:
        with sftp.open(log_path, "r") as f:
            f.seek(offset)
            raw = f.read()
        dec = RemoteOutputDecoder()
        return offset + len(raw), dec.feed(raw) + dec.flush()
    except FileNotFoundError:
        return offset, ""
    finally:
        sftp.close()
