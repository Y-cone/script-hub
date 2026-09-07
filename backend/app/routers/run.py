from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from ..database import get_db, async_session
from ..models.script import Script
from ..models.run_history import RunHistory
from ..schemas.run import RunRequest, RunResponse, RunHistoryOut, RunHistoryListOut
from ..services.executor import executor
from ..services.envcheck import check_environment
import json
import asyncio
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/run", tags=["run"])


@router.post("", response_model=RunResponse)
async def run_script(
    request: RunRequest,
    db: AsyncSession = Depends(get_db)
):
    """执行脚本（本机或远程设备，由 device_id 决定）"""
    result = await db.execute(select(Script).where(Script.id == request.script_id))
    script = result.scalar_one_or_none()
    if not script:
        raise HTTPException(404, "Script not found")
    
    if script.dangerous and not request.confirm_dangerous:
        raise HTTPException(400, "高危脚本需要确认执行")

    # 目标设备（远程执行）
    device = None
    if request.device_id:
        from ..models.device import Device as DeviceModel
        dres = await db.execute(select(DeviceModel).where(DeviceModel.id == request.device_id))
        device = dres.scalar_one_or_none()
        if not device:
            raise HTTPException(404, "Device not found")

    if not device:
        # 本机环境检测前置：不达标时必须 confirm_env=True 才放行
        env_checks = check_environment(script)
        env_checks_ok = all(c["ok"] for c in env_checks)
        if not env_checks_ok and not request.confirm_env:
            raise HTTPException(
                409,
                detail={
                    "message": "环境检测未达标，请确认后重试（confirm_env=true）",
                    "checks": env_checks,
                },
            )
    
    # 先创建 run_history 记录
    command = (executor._build_command(script, request.parameters or {})
               if not device else f"ssh {device.host}: {script.name}")
    run_history = RunHistory(
        script_id=script.id,
        parameters=json.dumps(request.parameters or {}, ensure_ascii=False),
        command=command,
        status="running",
        started_at=datetime.now(),
        device_id=device.id if device else None,
    )
    db.add(run_history)
    await db.commit()
    await db.refresh(run_history)
    
    # 立即启动后台任务（真正的并发，不阻塞事件循环）
    async def _run():
        try:
            if device:
                await executor.execute_remote(
                    script=script,
                    device=device,
                    run_history_id=run_history.id,
                    parameters=request.parameters or {},
                    timeout=request.timeout or script.timeout or 0,
                )
            else:
                await executor.execute_script(
                    script=script,
                    run_history_id=run_history.id,
                    parameters=request.parameters or {},
                    working_dir=request.working_dir,
                    env_vars=request.env_vars,
                    timeout=request.timeout or script.timeout or 0
                )
        except Exception as e:
            logger.error(f"后台任务异常: {e}")
            # 确保状态更新为 failed
            await executor._update_db(
                run_history.id,
                status="failed",
                exit_code=-1,
                output=f"执行异常: {str(e)}",
                finished_at=datetime.now()
            )
    
    asyncio.create_task(_run())
    
    return RunResponse(
        id=run_history.id,
        status="running",
        command=command,
        message="远程执行已启动" if device else "脚本执行已启动"
    )


@router.get("/history", response_model=RunHistoryListOut)
async def get_run_history(
    page: int = 1,
    page_size: int = 20,
    script_id: int = None,
    schedule_id: int = None,
    device_id: int = None,
    db: AsyncSession = Depends(get_db)
):
    """获取运行历史。schedule_id/device_id 用于筛选特定调度/设备的记录。"""
    query = select(RunHistory)
    count_query = select(func.count(RunHistory.id))
    
    if script_id:
        query = query.where(RunHistory.script_id == script_id)
        count_query = count_query.where(RunHistory.script_id == script_id)
    
    if schedule_id:
        query = query.where(RunHistory.schedule_id == schedule_id)
        count_query = count_query.where(RunHistory.schedule_id == schedule_id)

    if device_id:
        query = query.where(RunHistory.device_id == device_id)
        count_query = count_query.where(RunHistory.device_id == device_id)
    
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0
    
    query = query.order_by(RunHistory.started_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = result.scalars().all()
    
    return RunHistoryListOut(items=items, total=total, page=page, page_size=page_size)


@router.get("/{run_id}/download")
async def download_run_log(run_id: int, db: AsyncSession = Depends(get_db)):
    """下载运行日志文件"""
    result = await db.execute(select(RunHistory).where(RunHistory.id == run_id))
    run_history = result.scalar_one_or_none()
    if not run_history:
        raise HTTPException(404, "Run history not found")
    if not run_history.output_file:
        raise HTTPException(404, "日志文件不存在")
    
    from fastapi.responses import FileResponse
    import os
    
    if not os.path.exists(run_history.output_file):
        raise HTTPException(404, "日志文件已被删除")
    
    return FileResponse(
        run_history.output_file,
        filename=f"run_{run_id}.log",
        media_type="text/plain"
    )


@router.post("/{run_id}/kill")
async def kill_run(run_id: int):
    """终止运行中的脚本 — 不查DB，直接杀进程"""
    success = await executor.kill_process(run_id)
    if success:
        # 更新DB状态
        async with async_session() as db:
            result = await db.execute(select(RunHistory).where(RunHistory.id == run_id))
            rh = result.scalar_one_or_none()
            if rh:
                rh.status = "killed"
                rh.finished_at = datetime.now()
                if rh.started_at:
                    rh.duration = (rh.finished_at - rh.started_at).total_seconds()
                await db.commit()
        return {"message": "脚本已终止"}
    else:
        raise HTTPException(400, "脚本未在运行中或已结束")


@router.websocket("/ws/{run_id}")
async def websocket_run_output(websocket: WebSocket, run_id: int):
    """WebSocket实时输出"""
    await websocket.accept()
    
    try:
        while True:
            await asyncio.sleep(0.2)  # 更快轮询以配合实时输出
            
            async with async_session() as db:
                result = await db.execute(select(RunHistory).where(RunHistory.id == run_id))
                rh = result.scalar_one_or_none()
                
                if not rh:
                    await websocket.send_json({"error": "运行记录不存在"})
                    break
                
                await websocket.send_json({
                    "status": rh.status,
                    "output": rh.output or "",
                    "exit_code": rh.exit_code,
                    "command": rh.command or ""
                })
                
                if rh.status != "running":
                    break
                    
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket错误: {e}")
        try:
            await websocket.send_json({"error": str(e)})
        except:
            pass
