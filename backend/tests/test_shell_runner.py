"""Shell 覆盖白名单自检（SPEC §7.3 · V5-G v2.2）。

跑法：cd backend && .venv/bin/python tests/test_shell_runner.py
（批次 AJ 起也由 `pytest tests` 收集执行，见文件末尾 test_main_selfcheck()）
唯一职责：壳选项必须只有平台白名单内的值能通过，其余一律 ValueError（路由器据此回 400）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.executor import SHELL_RUNNERS, resolve_runner  # noqa: E402


def main() -> int:
    # 未指定 = 走 category 默认分派（覆盖不得凭空生效）
    assert resolve_runner(None, False) is None
    assert resolve_runner("", True) is None

    # 白名单内 → 返回运行器前缀
    assert resolve_runner("bash", False) == "bash"
    assert resolve_runner("python3", False) == "python3"
    assert resolve_runner("cmd", True) == "cmd /c"
    ps, pw = resolve_runner("powershell", True), resolve_runner("pwsh", True)
    assert ps is not None and ps.startswith("powershell -NoProfile")
    assert pw is not None and pw.startswith("pwsh")

    # 跨平台/非法 → 必须拒绝（这是 400 而非静默降级的保证）
    for bad, is_win in (("cmd", False), ("powershell", False), ("zsh", True), ("sh", True), ("xxx", False)):
        try:
            resolve_runner(bad, is_win)
        except ValueError:
            continue
        raise AssertionError(f"未拒绝 {bad!r} on {'win32' if is_win else 'unix'}")

    print("PASS | 白名单:", {k: sorted(v) for k, v in SHELL_RUNNERS.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def test_main_selfcheck():
    """把原本只走 __main__ 的自检接回 pytest 收集（此前 pytest 从不执行本文件）。
    返回 0 = 全过；失败时 main() 内部直接抛 AssertionError。无副作用（纯内存断言）。"""
    assert main() == 0
