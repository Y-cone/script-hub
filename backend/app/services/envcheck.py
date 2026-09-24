"""环境检测与系统信息采集"""
import platform
import socket
import shutil
import json
import sys
import re
import subprocess
from typing import Optional
from ..models.script import Script


# 平台兼容矩阵：脚本类型 → 可运行的平台
# `.sh` 在 Windows 上**不是无条件可跑**：要目标机装有 Git Bash/MSYS2/WSL 的 bash
# （executor.win_script_cmd 的 has_bash 分支同判据，无 bash 时显式拒绝执行）。
# 所以这里不写 "any"（会把没装 Git Bash 的 Windows 误报成可跑），而是保留 unix 原生要求，
# 再由 ALT_RUNTIME_RULES 描述「非原生平台 + 装了该运行时即可跑」这个例外。
PLATFORM_RULES = {
    "python": ("any", "跨平台"),
    "shell": ("unix", "Unix 原生（Linux/macOS）；Windows 需 Git Bash/MSYS2/WSL"),
    "bat": ("windows", "仅 Windows"),
    "powershell": ("windows", "仅 Windows"),
}

# 非原生平台上的例外：脚本类型 → 运行时可执行名（装上即可跨平台运行）
ALT_RUNTIME_RULES = {"shell": "bash"}

# 运行时版本探测命令
_VERSION_CMDS = {
    "python": ["python", "--version"],
    "python3": ["python3", "--version"],
    "node": ["node", "--version"],
    "bash": ["bash", "--version"],
    "powershell": ["powershell", "$PSVersionTable.PSVersion.ToString()"],
    # 仅保留脚本执行所需运行时；git/java 与执行路径无关，不探测
}


def current_platform() -> str:
    return "windows" if sys.platform.startswith("win") else "unix"


def has_runtime(exe: str) -> bool:
    """运行时是否在**本机** PATH 上（用于 ALT_RUNTIME_RULES 的例外判定）。

    与 executor._build_command 的本地执行判据一致：它也是靠 PATH 解析 `bash <脚本>`。
    ⚠️ 只对「本机」判据成立（远程的对应物是 probe 缓存里 runtimes 条目的 installed，
    见 _alt_runtime_available）—— 拿它判远程设备等于拿 Server 的 PATH 判目标机。
    """
    return shutil.which(exe) is not None


def platform_family(raw: Optional[str]) -> Optional[str]:
    """探测到的平台串 → envcheck 的二分类（windows/unix）；空值 → None（**无结论**）。

    `ssh_service.remote_probe` 给的是 win32（Windows 分支）/ unix / windows（uname 里带
    microsoft/windows 的 WSL 分支）—— 与 `detect_platform` 的 win32/unix 同一套口径。
    平台判据（PLATFORM_RULES）只认 windows/unix，这里是**唯一**的归一化点。
    """
    if not raw:
        return None
    return "windows" if str(raw).lower().startswith("win") else "unix"


def _alt_runtime_available(exe: str, device_runtimes: Optional[list[dict]]) -> Optional[bool]:
    """**远端设备**上替代运行时是否可用：True/False，**None = 无结论**（探测结论里没这一项）。

    判据 = probe 缓存 runtimes 条目的 `installed`（remote_probe 的原文结论），不是
    `shutil.which`（那是 Server 本机的 PATH，批次 AO 修的正是这个错判来源）。
    None 的含义与 `.sh` 三态一致：**探过但没覆盖这一项 ≠ 没装** → 调用方不许据此判红。
    """
    ent = next((r for r in (device_runtimes or []) if r.get("name") == exe), None)
    return None if ent is None else bool(ent.get("installed"))


def system_info() -> dict:
    """采集本机信息（主机名/OS/IP/架构 + 运行时版本）"""
    # IP 采集（尽力而为）
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        try:
            ips = [x[4][0] for x in socket.getaddrinfo(socket.gethostname(), None) if x[4]]
        except Exception:
            ips = []

    # 运行时版本探测（顺带被 env-check 复用）
    runtimes = probe_versions()

    return {
        "hostname": socket.gethostname(),
        "os": platform.system(),
        "os_version": platform.release(),
        "arch": platform.machine(),
        "platform": current_platform(),
        "ip": ips[0] if ips else "",
        "runtimes": runtimes,
    }


def probe_versions() -> list[dict]:
    """探测常见运行时是否存在及版本"""
    result = []
    for name, cmd in _VERSION_CMDS.items():
        exe = shutil.which(cmd[0])
        if not exe:
            result.append({"name": name, "installed": False, "version": None})
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            out = (r.stdout or r.stderr).strip().splitlines()
            ver = out[0].strip() if out else ""
            # 提取第一段版本号（如 "Python 3.12.1" → "3.12.1"）
            result.append({"name": name, "installed": True, "version": ver})
        except Exception as e:
            result.append({"name": name, "installed": True, "version": f"探测失败: {e}"})
    return result


def check_environment(script: Script,
                      device_platform: Optional[str] = None,
                      device_runtimes: Optional[list[dict]] = None) -> list[dict]:
    """
    检测脚本运行环境（平台兼容性 + 运行时版本）。
    返回检查项列表：{"type","name","required","actual","ok","detail"}
    ok=True 达标；ok=False 需用户确认后放行。

    ⚠️ **判据对象由调用方指定**（批次 AO）：
      · 两个 device_* 都不传（默认）→ 判 **Server 本机**。只有本机执行前置该这么用
        （run.py 的 `if not device:` 分支），行为与旧版逐字一致。
      · 传了 → 判**目标设备**：`device_platform` = ssh_service probe 缓存里的 platform，
        `device_runtimes` = 同一份缓存里的 runtimes（remote_probe 的原文结论）。设备上下文下的
        「环境检测」按钮必须走这条 —— 否则 Windows 设备会被判成 Server 本机的 unix
        （批次 AO 修的缺口）。
        `device_runtimes` 传 `[]` 与传 None 在判据上等价（都是「没覆盖到这一项」＝无结论）；
        区别只在调用方的语义（None=没探过，[]=探过但没拿到条目，见 cache_probe 的三态注释）。

    远端模式的两条铁律：
      · 平台比 `device_platform`、替代运行时比 `device_runtimes[*].installed`，**绝不回落**
        `shutil.which` / `current_platform()`（那是 Server 的 PATH 与平台）。
      · **无数据 ≠ 没装**（SPEC §7.2-#2 / `.sh` 三态）：拿不到结论时（平台未知 / runtimes 里
        没有这一项）→ ok=True + detail 写明「未取得结论」，不判红、不编造。
    """
    checks = []
    # 远端模式 = 调用方给了目标设备的探测结论（否则一律按本机）
    remote = device_platform is not None or device_runtimes is not None

    # 1. 平台兼容性（静态 + 例外实测）
    rule = PLATFORM_RULES.get(script.category, ("any", "未知类型"))
    want_platform, desc = rule
    cur = platform_family(device_platform) if remote else current_platform()
    alt = ALT_RUNTIME_RULES.get(script.category)

    if cur is None:
        # 远端没拿到平台结论（探测半途失败）→ 无结论，不判红、也不拿本机平台顶替
        ok = True
        detail = "未取得目标设备的平台结论 —— 不拦截（可在设备页「测试连接」后重试）"
    else:
        ok = (want_platform == "any") or (want_platform == cur)
        if ok:
            detail = "可执行"
        elif alt:
            alt_ok = _alt_runtime_available(alt, device_runtimes) if remote else has_runtime(alt)
            if alt_ok is True:
                # 非原生平台但装了替代运行时（Windows + .sh + Git Bash）→ 真能跑，不该判死
                ok = True
                detail = f"可执行（当前为 {cur}，经 {alt} 运行）"
            elif alt_ok is None:
                # 探过但没覆盖这一项 = 无结论 ≠ 没装 → 不判红（与 .sh 三态同口径）
                ok = True
                detail = (f"未取得 {alt} 的探测结论（当前为 {cur}）—— 不拦截，"
                          f"执行时如缺 {alt} 会由运行器报错")
            else:
                detail = f"此脚本需在 {desc} 环境运行，当前为 {cur}，且未检测到 {alt}"
        else:
            detail = f"此脚本需在 {desc} 环境运行，当前为 {cur}"
    checks.append({
        "type": "platform",
        "name": f"平台兼容性 ({script.category})",
        "required": desc,
        "actual": cur,
        "ok": ok,
        "detail": detail,
    })

    # 2. 运行时版本（探测脚本 env_requests 字段，如 {"python": ">=3.8"}）
    reqs = _parse_env_requests(script)
    if reqs:
        # 远端用设备探测结论（原文），本机才现探 —— 两条路都产 {name: {installed, version}}
        probe_map = ({r["name"]: r for r in (device_runtimes or [])} if remote
                     else {r["name"]: r for r in probe_versions()})
        for name, requirement in reqs.items():
            probed = probe_map.get(name)
            if not probed:
                if remote:
                    # 设备探测结论里没有这一项 = **无结论**（不是「没装」）→ 不判红
                    checks.append({
                        "type": "runtime",
                        "name": name,
                        "required": requirement,
                        "actual": None,
                        "ok": True,
                        "detail": f"目标设备未探测到 {name}（无结论）—— 不拦截，执行时如缺失会报错",
                    })
                else:
                    checks.append({
                        "type": "runtime",
                        "name": name,
                        "required": requirement,
                        "actual": None,
                        "ok": False,
                        "detail": f"未检测到 {name}（可能未安装或不在 PATH）",
                    })
                continue
            if not probed["installed"]:
                checks.append({
                    "type": "runtime",
                    "name": name,
                    "required": requirement,
                    "actual": None,
                    "ok": False,
                    "detail": f"未安装 {name}",
                })
                continue
            version = probed.get("version") or ""
            checks.append({
                "type": "runtime",
                "name": name,
                "required": requirement,
                "actual": version,
                "ok": _version_ok(version, requirement),
                "detail": f"检测到 {version}，要求 {requirement}",
            })

    return checks


def _parse_env_requests(script: Script) -> dict:
    """从脚本的 env_requests JSON 字段解析环境要求"""
    if script.env_requests:
        try:
            d = json.loads(script.env_requests)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    return {}


def _version_ok(actual: str, requirement: str) -> bool:
    """简单语义化版本比较：支持 >=, >, ==, <, <= 前缀。"""
    req = requirement.strip()
    m = re.match(r"(>=|<=|>|<|==)?\s*([\d.]+)", req)
    if not m:
        return True  # 无法解析需求，不阻碍（放行）
    op = m.group(1) or "=="
    req_ver = _ver_tuple(m.group(2))
    # 用 search 提取实际版本号（容忍 "v24.15.0" / "Node.js v24.15.0" 前缀）
    actual_m = re.search(r"\d+(?:\.\d+)*", actual or "")
    if not actual_m:
        return True  # 探测到的版本无数字，无法比较则放行
    act_ver = _ver_tuple(actual_m.group(0))

    def cmp_(a, b):
        return (a > b) - (a < b)

    c = cmp_(act_ver, req_ver)
    return {
        ">": c > 0,
        ">=": c >= 0,
        "<": c < 0,
        "<=": c <= 0,
        "==": c == 0,
    }.get(op, True)


def _ver_tuple(v: str) -> tuple:
    return tuple(int(x) for x in v.split(".") if x.isdigit())