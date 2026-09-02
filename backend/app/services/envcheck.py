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
PLATFORM_RULES = {
    "python": ("any", "跨平台"),
    "shell": ("unix", "仅 Unix（Linux/macOS）"),
    "bat": ("windows", "仅 Windows"),
    "powershell": ("windows", "仅 Windows"),
}

# 运行时版本探测命令
_VERSION_CMDS = {
    "python": ["python", "--version"],
    "python3": ["python3", "--version"],
    "node": ["node", "--version"],
    "bash": ["bash", "--version"],
    "powershell": ["powershell", "$PSVersionTable.PSVersion.ToString()"],
    "git": ["git", "--version"],
    "java": ["java", "-version"],
}


def current_platform() -> str:
    return "windows" if sys.platform.startswith("win") else "unix"


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


def check_environment(script: Script, env_requests: Optional[dict] = None) -> list[dict]:
    """
    检测脚本运行环境（平台兼容性 + 运行时版本）。
    返回检查项列表：{"type","name","required","actual","ok","detail"}
    ok=True 达标；ok=False 需用户确认后放行。
    """
    checks = []

    # 1. 平台兼容性（静态）
    rule = PLATFORM_RULES.get(script.category, ("any", "未知类型"))
    want_platform, desc = rule
    cur = current_platform()
    ok = (want_platform == "any") or (want_platform == cur)
    checks.append({
        "type": "platform",
        "name": f"平台兼容性 ({script.category})",
        "required": desc,
        "actual": cur,
        "ok": ok,
        "detail": "可执行" if ok else f"此脚本需在 {desc} 环境运行，当前为 {cur}",
    })

    # 2. 运行时版本（探测 env_requests，如 {"python": ">=3.8"}）
    reqs = env_requests or _parse_env_requests(script)
    if reqs:
        probe_map = {r["name"]: r for r in probe_versions()}
        for name, requirement in reqs.items():
            probed = probe_map.get(name)
            if not probed:
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