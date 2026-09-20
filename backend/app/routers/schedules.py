from fastapi import APIRouter, Depends, HTTPException
import json
import os
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..database import get_db
from ..models.schedule import Schedule
from ..models.script import Script
from ..models.device import Device
from ..schemas.schedule import ScheduleOut, ScheduleCreate, ScheduleUpdate, ScheduleListOut
from ..services.scheduler_service import scheduler_service, _run_scheduled
from ..services.sched_delegate import (
    manager_for, deploy_script, mark_dirty, map_cron_to_schtasks, SchtasksManager)
from ..services.ssh_service import pool as ssh_pool

# V5-E：每调度的 cron.log 拉取偏移（内存态；重启后从 0 重拉全量一次，可接受）
_pull_offset: dict[int, int] = {}

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


async def _sync_device_schedule(db: AsyncSession, sched: Schedule):
    """V5-E：exec_location=device 时，把任务写入目标设备系统调度器（部署+crontab/schtasks）。"""
    if sched.exec_location != "device":
        return
    if not sched.device_id:
        raise HTTPException(400, "下放任务必须选择目标设备")
    if not sched.cron_expr:
        raise HTTPException(400, "下放仅支持 cron 表达式任务（interval 不支持）")
    device = (await db.execute(select(Device).where(Device.id == sched.device_id))).scalar_one_or_none()
    if not device:
        raise HTTPException(404, "目标设备不存在")
    script = (await db.execute(select(Script).where(Script.id == sched.script_id))).scalar_one_or_none()
    if not script:
        raise HTTPException(404, "脚本不存在")

    mgr = manager_for(device, await ssh_pool.get(device))
    # schtasks 受限映射预检（不支持提前报错，不产生半写状态）
    if isinstance(mgr, SchtasksManager) and map_cron_to_schtasks(sched.cron_expr) is None:
        raise HTTPException(400, f"cron 表达式 '{sched.cron_expr}' 无法映射为 Windows 计划任务"
                                 "（仅支持每天/每周形态），请改用本工具内执行")

    client = await ssh_pool.get(device)
    remote_exe = await deploy_script(script, device, client)
    try:
        p = json.loads(sched.parameters or "{}")
    except Exception:
        p = {}
    args_str = " ".join(f"{k} {v}" for k, v in p.items() if v not in (None, "")) if isinstance(p, dict) else ""
    await mgr.upsert(sched, script, remote_exe, args_str)


async def _remove_device_schedule(db: AsyncSession, sched: Schedule):
    """从目标设备系统调度器移除（切回 local / 删除任务时）。"""
    if sched.exec_location != "device" or not sched.device_id:
        return
    await _remove_device_schedule_at(db, sched.device_id, sched.id)


async def _remove_device_schedule_at(db: AsyncSession, device_id: int | None, schedule_id: int):
    """按旧 device_id 移除设备端调度（update 时字段已被改写，需用旧值）。"""
    if not device_id:
        return
    device = (await db.execute(select(Device).where(Device.id == device_id))).scalar_one_or_none()
    if not device:
        return
    try:
        client = await ssh_pool.get(device)
        await manager_for(device, client).remove(schedule_id)
    except Exception as e:
        # 设备不可达时不阻塞本地操作（下次对账会发现漂移）
        import logging
        logging.getLogger(__name__).warning(f"移除设备端调度失败(设备不可达?): {e}")


async def _reload_schedule(db: AsyncSession, schedule_id: int):
    """创建/更新后重注册：local → APScheduler；device → 写入设备系统调度器"""
    sched = (await db.execute(select(Schedule).where(Schedule.id == schedule_id))).scalar_one_or_none()
    if not sched:
        return
    # 本地调度器：始终先移除（切换位置/停用时不再触发）
    scheduler_service.remove_job(schedule_id)
    if sched.exec_location == "device":
        if sched.enabled:
            await _sync_device_schedule(db, sched)
        else:
            # 停用 = 从设备端移除（标记块删除；重新启用会重写）
            await _remove_device_schedule(db, sched)
        return
    # local
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
        exec_location=data.exec_location or "local",
    )
    db.add(sched)
    await db.commit()
    await db.refresh(sched)
    if sched.exec_location == "device":
        await _sync_device_schedule(db, sched)
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
    old_location = sched.exec_location or "local"
    old_device_id = sched.device_id
    sched.cron_expr = new_cron
    sched.interval_seconds = new_interval
    for field, value in data.model_dump(exclude_unset=True).items():
        if field in ("cron_expr", "interval_seconds"):
            continue
        setattr(sched, field, value)
    await db.commit()
    await db.refresh(sched)
    # 位置/内容变化 → 从旧位置移除（用旧的 location/device_id——setattr 后已变）
    new_location = sched.exec_location or "local"
    if old_location == "device":
        if old_location != new_location or sched.device_id != old_device_id:
            await _remove_device_schedule_at(db, old_device_id, schedule_id)
    if new_location == "device":
        await _sync_device_schedule(db, sched)
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
    await _remove_device_schedule(db, sched)
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

# ---------------- V5-E：调度下放（对账 + 历史拉取） ----------------

@router.get("/{schedule_id}/delegate-audit")
async def audit_delegate(schedule_id: int, db: AsyncSession = Depends(get_db)):
    """漂移对账：远端标记块实际状态 vs DB。status: ok/disabled/missing（期望=启用时应存在）。"""
    sched = (await db.execute(select(Schedule).where(Schedule.id == schedule_id))).scalar_one_or_none()
    if not sched:
        raise HTTPException(404, "Schedule not found")
    if sched.exec_location != "device" or not sched.device_id:
        return {"status": "not-delegated"}
    device = (await db.execute(select(Device).where(Device.id == sched.device_id))).scalar_one_or_none()
    if not device:
        raise HTTPException(404, "目标设备不存在")
    try:
        client = await ssh_pool.get(device)
        remote = await manager_for(device, client).audit()
    except Exception as e:
        raise HTTPException(502, f"设备不可达: {e}")
    expected = "ok" if sched.enabled else "disabled"
    actual = remote.get(schedule_id, "missing")
    return {"schedule_id": schedule_id, "expected": expected,
            "actual": actual, "drift": actual != expected}


@router.post("/{schedule_id}/delegate-rewrite")
async def rewrite_delegate(schedule_id: int, db: AsyncSession = Depends(get_db)):
    """一键「以工具为准重写」：重新部署 + 重写标记块。"""
    sched = (await db.execute(select(Schedule).where(Schedule.id == schedule_id))).scalar_one_or_none()
    if not sched:
        raise HTTPException(404, "Schedule not found")
    await _sync_device_schedule(db, sched)
    return {"ok": True, "message": "已按工具配置重写设备端调度"}


@router.post("/{schedule_id}/delegate-pull")
async def pull_delegate_history(schedule_id: int, db: AsyncSession = Depends(get_db)):
    """拉模式历史回传：SFTP 拉取设备端 cron.log 增量解析入库（source=device-cron，非实时）。"""
    sched = (await db.execute(select(Schedule).where(Schedule.id == schedule_id))).scalar_one_or_none()
    if not sched:
        raise HTTPException(404, "Schedule not found")
    if sched.exec_location != "device" or not sched.device_id:
        raise HTTPException(400, "该任务不是下放任务")
    device = (await db.execute(select(Device).where(Device.id == sched.device_id))).scalar_one_or_none()
    if not device:
        raise HTTPException(404, "目标设备不存在")
    script = (await db.execute(select(Script).where(Script.id == sched.script_id))).scalar_one_or_none()
    if not script:
        raise HTTPException(404, "脚本不存在")

    from ..services.sched_delegate import deploy_dir, read_log_since
    client = await ssh_pool.get(device)
    log_path = f"{deploy_dir(script.id)}/cron.log"
    offset = _pull_offset.get(schedule_id, 0)
    new_offset, lines = await read_log_since(client, log_path, offset)
    imported = 0
    # 每条 cron 执行以命令回显行开始（脚本首行 echo 由部署包装器输出），简化解析：
    # 把整段 log 的每个「开始标记」间的内容存为一条历史（粗粒度，够用）
    if lines.strip():
        from datetime import datetime
        from ..models.run_history import RunHistory
        rh = RunHistory(
            script_id=script.id, parameters=sched.parameters or "{}",
            command=f"device-cron {device.name}: {script.name}",
            output=lines, status="success" if "Traceback" not in lines else "failed",
            is_scheduled=1, schedule_id=sched.id, device_id=device.id,
            started_at=datetime.now(), finished_at=datetime.now(),
        )
        db.add(rh)
        await db.commit()
        imported = 1
    _pull_offset[schedule_id] = new_offset
    return {"imported": imported, "offset": new_offset}



