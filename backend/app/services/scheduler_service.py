"""定时调度服务：管理 APScheduler 任务，调用执行引擎"""
import json
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from ..database import async_session
from ..models.schedule import Schedule
from ..models.script import Script
from ..models.run_history import RunHistory
from ..services.executor import executor, spawn_background

logger = logging.getLogger(__name__)

# 防并发执行：记录正在运行的 schedule_id 集合
_running: set[int] = set()


async def _run_scheduled(schedule_id: int, force: bool = False):
    """执行一次调度指定的脚本（复用 executor，标注 is_scheduled）。
    force=True 时跳过 enabled 检查（用于【立即执行】）。
    """
    if schedule_id in _running:
        logger.warning(f"调度 {schedule_id} 正在执行，跳过本次触发")
        return
    _running.add(schedule_id)
    try:
        async with async_session() as db:
            sched = (await db.execute(select(Schedule).where(Schedule.id == schedule_id))).scalar_one_or_none()
            if not sched:
                return
            if not force and not sched.enabled:
                return
            script = (await db.execute(select(Script).where(Script.id == sched.script_id))).scalar_one_or_none()
            if not script:
                logger.warning(f"调度 {schedule_id} 引用的脚本不存在（script_id={sched.script_id}），跳过本次触发")
                return

            # 目标设备（远程执行）
            device = None
            if sched.device_id:
                from ..models.device import Device
                device = (await db.execute(select(Device).where(Device.id == sched.device_id))).scalar_one_or_none()
                if not device:
                    logger.warning(f"调度 {schedule_id} 的目标设备不存在（id={sched.device_id}），跳过")
                    return

            try:
                parameters = json.loads(sched.parameters or "{}")
            except Exception:
                parameters = {}
            try:
                env = json.loads(sched.env_vars) if sched.env_vars else None
            except Exception:
                env = None

            command = (executor._build_command(script, parameters)
                       if not device else f"ssh {device.host}: {script.name}")
            rh = RunHistory(
                script_id=script.id,
                parameters=json.dumps(parameters, ensure_ascii=False),
                command=command,
                status="running",
                started_at=datetime.now(),
                is_scheduled=1,
                schedule_id=schedule_id,
                device_id=device.id if device else None,
            )
            db.add(rh)
            await db.commit()
            await db.refresh(rh)

            if device:
                spawn_background(executor.execute_remote(
                    script=script,
                    device=device,
                    run_history_id=rh.id,
                    parameters=parameters,
                    timeout=sched.timeout or script.timeout or 0,
                ))
            else:
                spawn_background(executor.execute_script(
                    script=script,
                    run_history_id=rh.id,
                    parameters=parameters,
                    working_dir=sched.working_dir,
                    env_vars=env,
                    timeout=sched.timeout or script.timeout or 0,
                ))
    finally:
        _running.discard(schedule_id)


class SchedulerService:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()

    def _trigger_for(self, sched: Schedule):
        """根据 cron/interval 生成 APScheduler trigger（二选一约束）"""
        if sched.cron_expr:
            try:
                return CronTrigger.from_crontab(sched.cron_expr)
            except Exception as e:
                logger.error(f"cron 表达式无效: {sched.cron_expr} ({e})")
                raise ValueError(f"无效的 cron 表达式: {sched.cron_expr}")
        if sched.interval_seconds:
            return IntervalTrigger(seconds=sched.interval_seconds)
        raise ValueError("需要提供 cron 表达式或间隔秒数")

    def add_job(self, sched: Schedule):
        if not sched.enabled:
            return
        trigger = self._trigger_for(sched)
        self.scheduler.add_job(
            _run_scheduled,
            trigger=trigger,
            args=[sched.id],
            id=f"sched_{sched.id}",
            replace_existing=True,
            name=sched.name,
        )
        logger.info(f"已注册调度: {sched.name}(id={sched.id})")

    def remove_job(self, schedule_id: int):
        job_id = f"sched_{schedule_id}"
        try:
            self.scheduler.remove_job(job_id)
        except Exception:
            pass

    async def start(self):
        """从 DB 加载所有启用的调度并注册（服务重启恢复）"""
        async with async_session() as db:
            scheds = (await db.execute(select(Schedule).where(Schedule.enabled.is_(True)))).scalars().all()
            for s in scheds:
                try:
                    self.add_job(s)
                except ValueError as e:
                    logger.warning(str(e))
        self.scheduler.start()
        logger.info(f"调度服务启动，注册 {len(self.scheduler.get_jobs())} 个任务")

    def shutdown(self):
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass


scheduler_service = SchedulerService()