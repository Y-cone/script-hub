"""调度下放的 runner 分派 + 平台判据（批次 AC ① ②）——离线断言，不联网、不建库。

① 现状 bug：`SchtasksManager.upsert` 一律拼 `python <脚本远端路径>`，.bat/.ps1/.sh 全被 python
   当输入文件执行 → 必失败（只有 .py 恰好正确）；crontab 侧同样假设「脚本自带可执行位/shebang」。
   修法：两处都改用 executor 里**与手动执行同一个分派点**（win_script_cmd / unix_script_cmd）。

② 平台判据：下放路径曾读 DB 字段 device.type（SPEC 注明该字段仅 UI 区分、可能标错/过期），
   与手动执行的实时探测（detect_platform）是两套判据；现在由调用方一次 `is_win_device()` 探测、
   管理器选择与上传转码共用**同一个值**（device.type 不再参与）。

跑法：cd backend && .venv/bin/python -m pytest tests/test_sched_delegate_dispatch.py -q
注意：引号层数不用肉眼数——`_plain()` 把反斜杠/引号换成 <BS>/<Q> 再比对。
"""
import asyncio
import importlib
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import app.services.sched_delegate as sd           # noqa: E402
import app.services.ssh_service as ssh_service     # noqa: E402
from app.models.device import Device               # noqa: E402
from app.models.schedule import Schedule           # noqa: E402
from app.models.script import Script               # noqa: E402
from app.routers import schedules as sched_router  # noqa: E402


def _plain(s: str) -> str:
    """反斜杠 → <BS>，双引号 → <Q>：比对带反斜杠的命令串时比数转义层数可靠。"""
    return s.replace("\\", "<BS>").replace('"', "<Q>")


def _device(dev_type: str) -> Device:
    return Device(id=4, name="d4", type=dev_type, host="h", port=22,
                  auth_type="password", username="u", auth_ref="r")


def _script(category: str, relative_path: str, tmp_path: Path | None = None) -> Script:
    return Script(id=4, name=Path(relative_path).name,
                  path=str((tmp_path or Path("/nonexistent")) / relative_path),
                  relative_path=relative_path, extension=Path(relative_path).suffix,
                  category=category, dependencies=None)


def _sched(cron="0 3 * * *", enabled=True, parameters="{}") -> Schedule:
    return Schedule(id=7, script_id=4, name="s", cron_expr=cron, interval_seconds=None,
                    enabled=enabled, parameters=parameters, env_vars=None,
                    working_dir=None, timeout=0, device_id=4, exec_location="device")


class _FakeSFTP:
    def __init__(self):
        self.written: dict[str, bytes] = {}
        self.mkdirs: list[str] = []

    def mkdir(self, path):
        self.mkdirs.append(path)

    def open(self, path, mode):
        assert mode == "wb", mode
        outer = self

        class _F:
            def write(self, data):
                outer.written[path] = data

            def close(self):
                pass

        return _F()

    def close(self):
        pass


class _FakeClient:
    def __init__(self):
        self.sftp = _FakeSFTP()

    def open_sftp(self):
        return self.sftp


def _live_ssh():
    """取 ssh_service 的**当前**模块对象（按 sys.modules 现取，缺则重新导入）。

    别的测试模块的 isolated fixture 会 `del sys.modules['app.*']`（见 test_settings.py:24），
    本文件顶部 `import ... as ssh_service` 拿到的对象随后就成了没人引用的死对象：sched_delegate
    里的**函数内** `from ..services.ssh_service import exec_command` 会在调用时重新导入一个
    **新**模块对象 → 打在死对象上的补丁等于没打，测试真去连 SSH（整目录跑必现 socket.gaierror，
    单文件跑侥幸过）。补丁必须落在被测代码调用时真正会解析到的那个对象上。
    """
    return importlib.import_module("app.services.ssh_service")


def _capture_exec(monkeypatch) -> list[str]:
    """替换 ssh_service.exec_command：记录下发的远端命令，不联网。"""
    cmds: list[str] = []

    async def fake_exec(device, command, timeout=120, output_cb=None, cancel_event=None):
        cmds.append(command)
        return 0, ""

    monkeypatch.setattr(_live_ssh(), "exec_command", fake_exec)
    # 兜网：万一还有没盖住的路径去取连接池 → 立刻炸在测试里，而不是真去连设备
    async def _no_net(device):
        raise AssertionError(f"测试试图建立 SSH 连接（host={device.host!r}）——mock 没盖住")

    monkeypatch.setattr(type(_live_ssh().pool), "get", _no_net)
    return cmds


# ── ① Windows 下放：/TR 必须按脚本类别分派运行器（不再一律 python）──────────────────────

def test_schtasks_bat_does_not_call_python(monkeypatch, tmp_path):
    """回归点：`.bat` 曾被拼成 `python C:\\...\\cleanup.bat`（必失败）→ 现在与手动执行同一条
    `cmd /c "cd /d <部署目录> && call "<脚本>" <参数>"`。"""
    monkeypatch.setenv("SCRIPTHUB_WIN_ANSI_CODEC", "cp936")
    cmds = _capture_exec(monkeypatch)
    script = _script("bat", "cleanup.bat", tmp_path)

    mgr = sd.SchtasksManager(_device("windows"), None)
    asyncio.run(mgr.upsert(_sched(), script, "$HOME/scripthub-deploy/4/cleanup.bat", "--mode full"))

    create = next(c for c in cmds if "/Create" in c)
    # 原 bug 的形态
    assert "python" not in create, f".bat 仍被交给 python：{create}"
    assert "python C:" not in create and "python %USERPROFILE%" not in create
    # 与手动执行同一构造：cmd /c "cd /d <目录> && call "<脚本>" <参数>"，内层引号转义、/TR 外层引号
    assert _plain(create) == (
        "schtasks /Create /TN <Q>scripthub-sched-7<Q> /TR <Q>cmd /c <BS><Q>cd /d "
        "%USERPROFILE%<BS>scripthub-deploy<BS>4 && call <BS><Q>%USERPROFILE%<BS>scripthub-deploy"
        "<BS>4<BS>cleanup.bat<BS><Q> --mode full<BS><Q> >> %USERPROFILE%<BS>scripthub-deploy"
        "<BS>4<BS>cron.log 2>&1<Q> /SC DAILY /F /ST 03:00"
    ), _plain(create)
    # 幂等：先删旧任务（既有行为不变）
    assert f'schtasks /Delete /TN "{mgr._name(7)}" /F' in cmds[0], cmds


def test_schtasks_ps1_py_sh_dispatch(monkeypatch, tmp_path):
    """其余类别各走对运行器（与手动执行口径一致）；无 bash 的 .sh 明确拒绝而不是硬跑"""
    monkeypatch.setenv("SCRIPTHUB_WIN_ANSI_CODEC", "cp936")
    dev = _device("windows")

    async def run(category, name, has_bash=None, args=""):
        cmds = _capture_exec(monkeypatch)
        if has_bash is not None:
            async def fake_bash(device):
                return has_bash
            monkeypatch.setattr(_live_ssh(), "detect_has_bash", fake_bash)
        script = _script(category, name, tmp_path)
        await sd.SchtasksManager(dev, None).upsert(
            _sched(), script, f"$HOME/scripthub-deploy/4/{name}", args)
        return next(c for c in cmds if "/Create" in c)

    create = asyncio.run(run("powershell", "clean.ps1"))
    assert "powershell -NoProfile -ExecutionPolicy Bypass -File" in create and "python" not in create

    create = asyncio.run(run("python", "job.py"))
    assert "python -X utf8" in create and "call " not in create

    create = asyncio.run(run("shell", "run.sh", has_bash=True))
    assert "bash " in create and "chcp 65001" in create and "python" not in create

    create = asyncio.run(run("shell", "run.sh", has_bash=False))
    assert "[边界]" in create and "python" not in create, create


# ── ① Unix 下放：cron 行必须带运行器与 cd（不再只写脚本路径）──────────────────────────

def _crontab_block(monkeypatch, script, remote, args="", enabled=True, cron="0 3 * * *") -> str:
    written: dict[str, str] = {}

    async def fake_read(self):
        return ""

    async def fake_write(self, content):
        written["content"] = content

    monkeypatch.setattr(sd.CrontabManager, "_read", fake_read)
    monkeypatch.setattr(sd.CrontabManager, "_write", fake_write)
    asyncio.run(sd.CrontabManager(_device("linux"), None).upsert(
        _sched(cron=cron, enabled=enabled), script, remote, args))
    return written["content"]


def test_crontab_sh_runner_cd_and_percent_escape(tmp_path, monkeypatch):
    """cron 行曾是 `<cron> $HOME/.../run.sh ...`（等于假设脚本可执行）→ 现在是
    `<cron> cd $HOME/<部署目录> && bash $HOME/.../run.sh ...`；参数里的 % 按 POSIX 转义。"""
    script = _script("shell", "run.sh", tmp_path)
    block = _crontab_block(monkeypatch, script, "$HOME/scripthub-deploy/4/run.sh", args="--out 50%")
    assert _plain(block) == (
        "# --- scripthub-begin:sched:7 ---\n"
        "0 3 * * * cd $HOME/scripthub-deploy/4 && bash $HOME/scripthub-deploy/4/run.sh "
        "--out 50<BS>% >> $HOME/scripthub-deploy/4/cron.log 2>&1\n"
        "# --- scripthub-end:sched:7 ---\n"
    ), _plain(block)
    # 旧形态（裸脚本路径、无解释器）不得再出现
    assert "0 3 * * * $HOME/scripthub-deploy/4/run.sh" not in block


def test_crontab_py_runner_and_idempotent_disable(tmp_path, monkeypatch):
    script = _script("python", "job.py", tmp_path)
    block = _crontab_block(monkeypatch, script, "$HOME/scripthub-deploy/4/job.py")
    assert ("0 3 * * * cd $HOME/scripthub-deploy/4 && python3 $HOME/scripthub-deploy/4/job.py "
            ">> $HOME/scripthub-deploy/4/cron.log 2>&1") in block, block
    # 不许出现 `$HOME/$HOME` 这类双重前缀（曾因 upsert 又拼一次 $HOME）
    assert "$HOME/$HOME" not in block

    off = _crontab_block(monkeypatch, script, "$HOME/scripthub-deploy/4/job.py", enabled=False)
    assert "# DISABLED 0 3 * * * cd $HOME/scripthub-deploy/4 && python3" in off, off
    assert "0 3 * * * cd" not in off.replace("# DISABLED 0 3 * * * cd", ""), off


def test_crontab_runner_mapping_matches_manual_execution():
    """cron 的运行器映射来自手动执行同一处（executor.UNIX_CATEGORY_RUNNERS），不是本文件的私货"""
    from app.services.executor import UNIX_CATEGORY_RUNNERS, unix_script_cmd
    assert unix_script_cmd("python", "/x/job.py") == "python3 /x/job.py"
    assert unix_script_cmd("shell", "/x/run.sh") == "bash /x/run.sh"
    assert unix_script_cmd("bat", "/x/a.bat") == "bash /x/a.bat"
    assert set(UNIX_CATEGORY_RUNNERS) == {"python", "shell", "powershell", "bat"}
    assert "UNIX_CATEGORY_RUNNERS" in (BACKEND / "app/services/sched_delegate.py").read_text(
        encoding="utf-8") or "unix_script_cmd" in (BACKEND / "app/services/sched_delegate.py").read_text(
        encoding="utf-8")


# ── ② 平台判据：唯一入口，且管理器与转码用同一个值 ────────────────────────────────────

def test_sync_device_schedule_uses_probe_not_device_type(monkeypatch, tmp_path):
    """device.type 说 windows、实时探测说 unix（字段标错场景）→ 必须按**探测**走 CrontabManager，
    且 deploy_script 收到同一个 is_win=False（上传转码与调度器注册不能各判一次）。"""
    monkeypatch.setenv("SCRIPTHUB_WIN_ANSI_CODEC", "cp936")
    script = _script("bat", "cleanup.bat", tmp_path)
    dev = _device("windows")   # ← DB 里标成 windows
    captured: dict[str, object] = {}

    async def fake_probe(device):
        captured["probed"] = True
        return False           # ← 实时探测：其实是 Linux

    async def fake_deploy(script, device, client, is_win):
        captured["deploy_is_win"] = is_win
        return "$HOME/scripthub-deploy/4/cleanup.bat"

    async def crontab_upsert(self, sched, script, remote, args_str=""):
        captured["manager"] = "crontab"

    async def schtasks_upsert(self, sched, script, remote, args_str=""):
        captured["manager"] = "schtasks"

    async def fake_get(self, device):
        return object()        # 池不参与本测试（deploy/upsert 都被替换）

    class _Res:
        def __init__(self, v):
            self._v = v

        def scalar_one_or_none(self):
            return self._v

    class _DB:
        """按调用顺序返回预置查询结果（本函数只查 Device、Script 两次）"""

        def __init__(self, values):
            self._values = list(values)

        async def execute(self, *a, **k):
            return _Res(self._values.pop(0))

    monkeypatch.setattr(sched_router, "is_win_device", fake_probe)
    monkeypatch.setattr(sched_router, "deploy_script", fake_deploy)
    monkeypatch.setattr(sd.CrontabManager, "upsert", crontab_upsert)
    monkeypatch.setattr(sd.SchtasksManager, "upsert", schtasks_upsert)
    monkeypatch.setattr(type(ssh_service.pool), "get", fake_get)

    asyncio.run(sched_router._sync_device_schedule(_DB([dev, script]), _sched()))

    assert captured.get("probed") is True, "下放路径没有走实时探测"
    assert captured["manager"] == "crontab", f"按 device.type 选错了管理器: {captured}"
    assert captured["deploy_is_win"] is False, "转码判据与调度器选择不是同一个值"


def test_manager_for_follows_flag_not_device_type():
    """manager_for 只看传入的 is_win（device.type 标错不影响）"""
    dev = _device("linux")     # 字段说 linux
    assert isinstance(sd.manager_for(dev, None, True), sd.SchtasksManager)
    assert isinstance(sd.manager_for(_device("windows"), None, False), sd.CrontabManager)


def test_transcoding_follows_is_win_not_device_type(monkeypatch, tmp_path):
    """deploy_script 的转码只看传入的 is_win（device.type 标反也不影响）"""
    monkeypatch.setenv("SCRIPTHUB_WIN_ANSI_CODEC", "cp936")
    src = tmp_path / "cleanup.bat"
    src.write_bytes("@echo off\necho 中文测试\n".encode("utf-8"))
    script = _script("bat", "cleanup.bat", tmp_path)
    script.path = str(src)

    for is_win, dev_type, want in (
        (True, "linux", "@echo off\r\necho 中文测试\r\n".encode("cp936")),   # Windows 目标 → ANSI+CRLF
        (False, "windows", src.read_bytes()),                              # Unix 目标 → 原样
    ):
        client = _FakeClient()
        path = asyncio.run(sd.deploy_script(script, _device(dev_type), client, is_win))
        assert path == "$HOME/scripthub-deploy/4/cleanup.bat", path
        got = client.sftp.written["scripthub-deploy/4/cleanup.bat"]
        assert got == want, f"is_win={is_win}（device.type={dev_type}）转码结果不符: {got!r}"


def test_no_hardcoded_python_runner_left():
    """防回归（源码级）：sched_delegate 里不得再写死 python 运行器，也不得另写一套映射"""
    src = (BACKEND / "app/services/sched_delegate.py").read_text(encoding="utf-8")
    assert 'f"python {exe_win}' not in src
    assert "win_script_cmd" in src and "unix_script_cmd" in src
    # 旧的读字段形态不得回来（docstring 里提到 device.type 是解释，不算代码）
    assert 'getattr(device, "type"' not in src and "device.type ==" not in src


def test_schtasks_tr_escaping_helper():
    """/TR 内层引号转义（.bat 的 call "..." 必须活着到 schtasks）"""
    assert sd._schtasks_tr('cmd /c "call "x.bat""', "%USERPROFILE%\\l.log") == \
        'cmd /c \\"call \\"x.bat\\"\\" >> %USERPROFILE%\\l.log 2>&1'


def test_read_log_since_decodes_ansi_and_advances_by_raw_bytes():
    """拉模式历史回传：Windows 的 cron.log 是 cp936 → 不许再满屏 U+FFFD；offset 按原始字节推进"""
    ansi = "任务执行失败".encode("cp936")
    raw = b"echo " + ansi + b"\r\n"

    class _F:
        def __init__(self):
            self._pos = 0

        def seek(self, off):
            self._pos = off

        def read(self):
            return raw[self._pos:]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _C:
        def open_sftp(self):
            outer = self

            class _S:
                def open(self, path, mode):
                    return _F()

                def close(self):
                    pass

            return _S()

    off, text = asyncio.run(sd.read_log_since(_C(), "scripthub-deploy/4/cron.log", 0))
    assert text == "echo 任务执行失败\r\n", repr(text)
    assert "\ufffd" not in text
    assert off == len(raw), "offset 未按原始字节推进（会出现重复/漏拉）"
