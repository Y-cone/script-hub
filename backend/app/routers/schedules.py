from fastapi import APIRouter, Depends, HTTPException
import os
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..database import get_db
from ..models.schedule import Schedule
from ..models.script import Script
from ..models.device import Device
from ..schemas.schedule import ScheduleOut, ScheduleCreate, ScheduleUpdate, ScheduleListOut
from ..services.scheduler_service import scheduler_service, _run_scheduled

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


async def _reload_schedule(db: AsyncSession, schedule_id: int):
    """创建/更新后重注册到调度器"""
    sched = (await db.execute(select(Schedule).where(Schedule.id == schedule_id))).scalar_one_or_none()
    if sched:
        scheduler_service.remove_job(schedule_id)
        if sched.enabled:
            try:
                scheduler_service.add_job(sched)
            except ValueError as e:
                raise HTTPException(400, str(e))


@router.get("", response_model=ScheduleListOut)
async def list_schedules(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Schedule, Script.name, Script.relative_path, Device.name)
        .join(Script, Script.id == Schedule.script_id, isouter=True)
        .join(Device, Device.id == Schedule.device_id, isouter=True)
        .order_by(Schedule.id)
    )
    rows = list(result.all())
    items = []
    for sched, script_name, rel_path, device_name in rows:
        sched.script_name = script_name
        # script_path = 脚本所在目录（不含文件名）；根目录显示 "/"
        sched.script_path = os.path.dirname(rel_path) or "/" if rel_path else None
        sched.device_name = device_name
        items.append(sched)
    return ScheduleListOut(items=items, total=len(items))


@router.post("", response_model=ScheduleOut)
async def create_schedule(data: ScheduleCreate, db: AsyncSession = Depends(get_db)):
    # cron/interval 二选一约束
    if not data.cron_expr and not data.interval_seconds:
        raise HTTPException(400, "必须提供 cron 表达式或间隔秒数")
    if data.cron_expr and data.interval_seconds:
        raise HTTPException(400, "cron 表达式与间隔秒数只能二选一")

    sched = Schedule(
        script_id=data.script_id,
        name=data.name,
        cron_expr=data.cron_expr,
        interval_seconds=data.interval_seconds,
        parameters=data.parameters or "{}",
        env_vars=data.env_vars,
        working_dir=data.working_dir,
        timeout=data.timeout or 0,
        device_id=data.device_id,
    )
    db.add(sched)
    await db.commit()
    await db.refresh(sched)
    await _reload_schedule(db, sched.id)
    await db.refresh(sched)
    return sched


@router.put("/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(schedule_id: int, data: ScheduleUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    sched = result.scalar_one_or_none()
    if not sched:
        raise HTTPException(404, "Schedule not found")

    new_cron = data.cron_expr if data.cron_expr is not None else sched.cron_expr
    new_interval = data.interval_seconds if data.interval_seconds is not None else sched.interval_seconds
    # 归一化空值：空串/0 视为未设置
    if new_cron == '':
        new_cron = None
    if not new_interval:
        new_interval = None
    if not new_cron and not new_interval:
        raise HTTPException(400, "必须提供 cron 表达式或间隔秒数")

    # 应用类型切换：写入非空字段，清空另一字段
    sched.cron_expr = new_cron
    sched.interval_seconds = new_interval
    for field, value in data.model_dump(exclude_unset=True).items():
        if field in ("cron_expr", "interval_seconds"):
            continue
        setattr(sched, field, value)
    await db.commit()
    await db.refresh(sched)
    await _reload_schedule(db, schedule_id)
    await db.refresh(sched)
    return sched


@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    sched = result.scalar_one_or_none()
    if not sched:
        raise HTTPException(404, "Schedule not found")
    scheduler_service.remove_job(schedule_id)
    await db.delete(sched)
    await db.commit()
    return {"message": "已删除"}


@router.post("/{schedule_id}/run")
async def run_schedule_now(schedule_id: int, db: AsyncSession = Depends(get_db)):
    """立即触发一次调度（独立于 APScheduler 触发）"""
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    sched = result.scalar_one_or_none()
    if not sched:
        raise HTTPException(404, "Schedule not found")
    await _run_scheduled(schedule_id, force=True)
    return {"message": "已触发", "id": schedule_id}