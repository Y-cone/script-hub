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


async def deploy_script(script: Script, device, client) -> str:
    """部署脚本+依赖到远端 scripthub-deploy/<id>/。返回远端脚本绝对路径（POSIX 风格）。"""
    base = deploy_dir(script.id)
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
    sftp.put(script.path, remote_script)

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
            sftp.put(str(src), f"{base}/{dep}")
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

    async def upsert(self, sched: Schedule, script: Script, remote_exe: str,
                     args_str: str = ""):
        """写入/更新标记块（幂等：先删旧块再插新块）。"""
        content = await self._read()
        sid = sched.id
        # 删旧块（含前后空行规整）
        begin, end = BEGIN_MARK.format(sid=sid), END_MARK.format(sid=sid)
        if begin in content:
            s = content.index(begin)
            e = content.index(end) + len(end)
            content = content[:s] + content[e:]
        log_file = f"$HOME/{deploy_dir(script.id)}/cron.log"
        block = (
            f"{begin}\n"
            f"{sched.cron_expr} {remote_exe} {args_str} >> {log_file} 2>&1\n"
            f"{end}\n"
        )
        if not sched.enabled:
            # 停用 = 注释块内执行行
            block = block.replace(
                f"{sched.cron_expr} {remote_exe}",
                f"# DISABLED {sched.cron_expr} {remote_exe}")
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

    async def upsert(self, sched: Schedule, script: Script, remote_exe: str,
                     args_str: str = ""):
        from ..services.ssh_service import exec_command
        mapping = map_cron_to_schtasks(sched.cron_expr or "")
        if mapping is None:
            raise ValueError(
                f"cron 表达式 '{sched.cron_expr}' 无法映射为 schtasks（仅支持每天/每周/一次），"
                "请改用本工具内执行")
        log_file = f"%USERPROFILE%\\{deploy_dir(script.id).replace('/', chr(92))}\\cron.log"
        # Windows 无 $HOME；exec_command 走 cmd → python 用 %USERPROFILE% 由 cmd 展开
        exe_win = remote_exe.replace("$HOME", "%USERPROFILE%")
        tr = f"python {exe_win} {args_str} >> {log_file} 2>&1"
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


def manager_for(device, client):
    """按目标平台返回调度器管理器。"""
    if str(getattr(device, "type", "")).lower() == "windows":
        return SchtasksManager(device, client)
    return CrontabManager(device, client)


async def read_log_since(client, log_path: str, offset: int) -> tuple[int, str]:
    """SFTP 拉取设备端 cron.log 自 offset 起的增量。返回 (新 offset, 增量文本)。"""
    sftp = client.open_sftp()
    try:
        with sftp.open(log_path, "r") as f:
            f.seek(offset)
            data = f.read().decode("utf-8", errors="replace")
            new_offset = offset + len(data.encode("utf-8"))
        return new_offset, data
    except FileNotFoundError:
        return offset, ""
    finally:
        sftp.close()
