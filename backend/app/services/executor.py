import asyncio
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

logger = logging.getLogger(__name__)

# 输出日志目录
RUNS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "runs"


class ScriptExecutor:
    """脚本执行引擎"""
    
    def __init__(self):
        self.running_processes: Dict[int, asyncio.subprocess.Process] = {}
        self.running_remote: Dict[int, asyncio.Event] = {}  # 远程执行的取消事件
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
        timeout: int = 0
    ):
        """执行脚本（后台任务，不阻塞API）"""
        collected_output = ""
        exit_code = -1
        status = "failed"
        output_file = None
        
        # 构建命令
        command = self._build_command(script, parameters)
        
        # 准备环境变量
        env = os.environ.copy()
        if env_vars:
            env.update(env_vars)
        
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
            async def read_stream(stream, is_stderr=False):
                nonlocal collected_output
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    text = line.decode('utf-8', errors='replace')
                    collected_output += text
                    
                    # 写入文件（降级模式下跳过）
                    self._write_log(output_file, text)
                    
                    # DB只存储尾部1000行（ponytail: 高频写，后续可改为批量/节流写）
                    lines = collected_output.split('\n')
                    if len(lines) > 1000:
                        db_output = '\n'.join(lines[-1000:])
                    else:
                        db_output = collected_output
                    await self._update_db(run_history_id, output=db_output)
            
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        read_stream(process.stdout),
                        read_stream(process.stderr, is_stderr=True)
                    ),
                    timeout=timeout if timeout > 0 else None
                )
                await process.wait()
                
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
        
        # 最终写回（DB存储尾部1000行）
        lines = collected_output.split('\n')
        db_output = '\n'.join(lines[-1000:]) if len(lines) > 1000 else collected_output

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
    
    def _build_command(self, script: Script, parameters: Dict[str, Any]) -> str:
        """构建执行命令（本机）"""
        if script.category == "python":
            cmd = f"python {script.path}"
        elif script.category == "shell":
            cmd = f"bash {script.path}"
        elif script.category == "bat":
            cmd = f"cmd /c {script.path}"
        elif script.category == "powershell":
            cmd = f"powershell -ExecutionPolicy Bypass -File {script.path}"
        else:
            cmd = script.path

        args = []
        for param_name, param_value in parameters.items():
            if isinstance(param_value, bool):
                if param_value:
                    args.append(param_name)
            elif param_value not in (None, ''):
                args.append(f"{param_name} {param_value}")

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

            async def _cb(text):
                nonlocal collected_output
                collected_output += text
                self._write_log(output_file, text)
                lines = collected_output.split('\n')
                db_out = '\n'.join(lines[-1000:]) if len(lines) > 1000 else collected_output
                await self._update_db(run_history_id, output=db_out)

            # SFTP 上传主脚本 + 依赖（保留相对目录结构）
            def _upload():
                sftp = client.open_sftp()
                try:
                    sftp.mkdir(remote_dir)
                except OSError:
                    pass
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
                sftp.put(script.path, remote_script)

                # 上传依赖清单文件
                imported_deps = []
                for dep in dependencies:
                    src = script_root / dep
                    if not src.exists():
                        continue
                    dst = f"{remote_dir}/{dep}"
                    _mkpath(dep)
                    sftp.put(str(src), dst)
                    imported_deps.append(dep)
                sftp.close()
                return imported_deps
            imported_deps = await asyncio.to_thread(_upload)

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

            # 根据 category 选择解释器（远端 Linux/Mac 用 python3，兼容无 python 别名的系统）
            runner = {
                "python": "python3", "shell": "bash", "powershell": "pwsh", "bat": "bash"
            }.get(script.category, "bash")
            remote_cmd = f"{runner} {remote_script} {arg_str}".strip()
            # 脚本所在远端目录（cd 至此，使脚本内相对引用如 source ./utils.sh 正确解析）
            remote_script_dir = remote_script.rsplit("/", 1)[0]

            # 执行（输出实时回调写 DB），拿到退出码；cd 至脚本所在目录使相对引用依赖正确
            code, _ = await exec_command(
                device,
                f"mkdir -p {remote_script_dir} && cd {remote_script_dir} && {remote_cmd}",
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

        lines = collected_output.split('\n')
        db_output = '\n'.join(lines[-1000:]) if len(lines) > 1000 else collected_output
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
