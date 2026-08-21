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
from ..database import async_session
from sqlalchemy import select
import logging

logger = logging.getLogger(__name__)


class ScriptExecutor:
    """脚本执行引擎"""
    
    def __init__(self):
        self.running_processes: Dict[int, asyncio.subprocess.Process] = {}
    
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
        
        # 构建命令
        command = self._build_command(script, parameters)
        
        # 准备环境变量
        env = os.environ.copy()
        if env_vars:
            env.update(env_vars)
        
        # 确定工作目录
        cwd = working_dir or script.working_dir or str(Path(script.path).parent)
        
        # 更新命令到数据库（立即）
        await self._update_db(run_history_id, command=command)
        
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
            
            # 实时读取输出并写入DB
            collected_output = f"\n$ {command}\n\n"
            await self._update_db(run_history_id, output=collected_output)
            
            # 并行读取 stdout 和 stderr
            async def read_stream(stream, is_stderr=False):
                nonlocal collected_output
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    text = line.decode('utf-8', errors='replace')
                    collected_output += text
                    # 每行写一次DB（ponytail: 高频写，后续可改为批量/节流写）
                    await self._update_db(run_history_id, output=collected_output)
            
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
                collected_output += f"\n[超时] 脚本执行超过 {timeout} 秒"
                exit_code = -1
                status = "timeout"
            except asyncio.CancelledError:
                collected_output += "\n[取消] 脚本被取消"
                exit_code = -1
                status = "killed"
                
        except Exception as e:
            logger.error(f"执行脚本失败: {e}")
            collected_output += f"\n执行失败: {str(e)}"
            exit_code = -1
            status = "failed"
            
        finally:
            self.running_processes.pop(run_history_id, None)
        
        # 最终写回
        await self._update_db(
            run_history_id,
            output=collected_output,
            exit_code=exit_code,
            status=status,
            finished_at=datetime.now()
        )
    
    async def kill_process(self, run_id: int) -> bool:
        """终止运行中的进程"""
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
        """构建执行命令"""
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


# 全局执行器实例
executor = ScriptExecutor()
