"""本机解释器解析：滤掉 Windows 上 `python3` 的 Microsoft Store 假垫片。

背景（批次 BM ①）：Windows 上 `shutil.which("python3")` 常常命中
`%LOCALAPPDATA%\Microsoft\\WindowsApps\\python3.EXE` —— 那是 Store 的 App Execution Alias，
跑起来只打印「Python 未安装，请到 Microsoft Store 安装」并以 9009 退出。which() 只按 PATH 命中，
分辨不出真假，于是本机所有 .py 执行全部失败（exit 9009）。

做法：候选逐个**实跑一次版本探测**，stdout 以 `3` 开头才算真身（垫片输出提示文本/超时/起不来都算假）；
全假则回退 `sys.executable`（打包含义 = sidecar 自身；此时本机确实没有可用 Python，脚本跑不起来，
但至少失败信息会落进运行输出，且"回退"这件事在日志里有 warning 可查）。
结果缓存（进程生命周期内不变，避免每次执行多两次子进程开销）。
"""
import functools
import logging
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)

# 候选顺序：python3 优先（Linux 多数发行版没有 python 别名），Windows 同样先 python3 再 python
PY_CANDIDATES = ("python3", "python")

# 探测命令：只打印主版本号，无副作用、不 import 用户代码
PROBE = "import sys;print(sys.version_info[0])"


def _is_real_python(exe: str) -> bool:
    """实跑 `exe -c <PROBE>`：stdout 以 b"3" 开头 = 真 Python 3；其余（商店提示文本、超时、起不来）都算假。"""
    try:
        r = subprocess.run([exe, "-c", PROBE], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.stdout.strip().startswith(b"3")


@functools.lru_cache(maxsize=1)
def resolve_python() -> str:
    """本机可用的 Python 解释器路径（绝对路径）；全部候选都是假的 → sys.executable。

    缓存：结果在进程生命周期内不变（`resolve_python.cache_clear()` 可供测试复位）。
    """
    for cand in PY_CANDIDATES:
        exe = shutil.which(cand)
        if not exe:
            continue
        if _is_real_python(exe):
            return exe
        logger.warning("%s 命中 %s，但不是可用的 Python 3（Store 垫片？），跳过", cand, exe)
    logger.warning("未找到可用的 %s，回退当前解释器 %s", "/".join(PY_CANDIDATES), sys.executable)
    return sys.executable
