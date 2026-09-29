from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from ..database import get_db, async_session
from ..models.script import Script
from ..models.run_history import RunHistory
from ..models.device import Device
from ..schemas.run import RunRequest, RunResponse, RunHistoryListOut
from typing import Optional
from ..services.executor import (executor, resolve_runner, spawn_background,
                                 validate_parameters, normalize_parameters)
from ..services.envcheck import check_environment
import json
import sys
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
        dres = await db.execute(select(Device).where(Device.id == request.device_id))
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
    else:
        # 远程执行前置：探测远端所需运行时，缺失时软提示（confirm_env=True 放行）
        from ..services.ssh_service import remote_probe
        try:
            probe = await remote_probe(device)
        except Exception:
            probe = {"platform": "unix", "runtimes": [], "os_info": ""}
        need = None
        if script.category == "python":
            need = "python"
        elif script.category == "powershell":
            need = "powershell"
        runtime_map = {r["name"]: r for r in probe.get("runtimes", [])}
        missing = (need and need in runtime_map and not runtime_map[need]["installed"]) or (need and need not in runtime_map)
        if missing and not request.confirm_env:
            raise HTTPException(
                409,
                detail={
                    "message": f"远程设备可能缺少 {need} 运行时（平台 {probe.get('platform')}），请确认后重试（confirm_env=true）",
                    "platform": probe.get("platform"),
                    "runtime": need,
                },
            )
    
    # 先创建 run_history 记录
    # V5-G（SPEC §2.4）：Shell 覆盖的平台合法性前置校验（executor 内仍是权威校验，此处只为给出清晰 400）
    # + N6：还要校验 shell 与脚本类型同族（.bat 不能用 bash 跑、.py 不能用 powershell 跑）
    if request.shell:
        is_win_target = (device.type == "windows") if device else (sys.platform == "win32")
        try:
            resolve_runner(request.shell, is_win_target, script.category)
        except ValueError as e:
            raise HTTPException(400, str(e))

    # N1：入参类型按脚本元数据（parser 产物）校验——int 型传非数字此前被 200 接受，脚本跑到
    # argparse 才炸；在分派前拦掉，错误里带参数名（本机/远程共用同一份命令构造）
    # BQ②：裸键归一化——`count` 与元数据 `--count` 匹配；未知键 → 400（此前裸键绕过校验直落 argparse）
    try:
        request.parameters = normalize_parameters(script, request.parameters or {})
        validate_parameters(script, request.parameters)
    except ValueError as e:
        raise HTTPException(400, str(e))

    command = (executor._build_command(script, request.parameters or {}, shell=request.shell)
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
                    shell=request.shell,
                )
            else:
                await executor.execute_script(
                    script=script,
                    run_history_id=run_history.id,
                    parameters=request.parameters or {},
                    working_dir=request.working_dir,
                    env_vars=request.env_vars,
                    timeout=request.timeout or script.timeout or 0,
                    shell=request.shell,
                )
        except Exception as e:
            logger.error(f"后台任务异常: {e}")
            # 确保状态更新为 failed（终态三件套齐全：status/finished_at/duration）
            now = datetime.now()
            await executor._update_db(
                run_history.id,
                status="failed",
                exit_code=-1,
                output=f"执行异常: {str(e)}",
                finished_at=now,
                duration=(now - run_history.started_at).total_seconds() if run_history.started_at else None,
            )
    
    spawn_background(_run())
    
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
    status: Optional[str] = None,
    source: Optional[str] = None,
    schedule_id: int = None,
    device_id: int = None,
    from_time: Optional[datetime] = None,
    to_time: Optional[datetime] = None,
    preview: bool = False,
    db: AsyncSession = Depends(get_db)
):
    """获取运行历史。schedule_id/device_id 用于筛选特定调度/设备的记录。

    V5-G（SPEC §7.3）：from/to 按时间窗筛选（周历视图按周取事件）；
    preview=true 时 output 截断为前 2KB（列表首屏体积；抽屉以选中行数据为准）。
    缺省行为与旧版一致（零破坏）。
    """
    # 筛选条件集中成一条列表，**同时**作用于 count 与 items——
    # 此前 items 的 query 在第 205 行被重建且丢掉全部 where（total 对、行数错：筛 status=timeout 也会返回满页记录）
    filters = []
    if script_id:
        filters.append(RunHistory.script_id == script_id)
    # status=success|failed|running|killed|timeout（列表「状态」筛选；此前后端完全忽略=假控件）
    if status:
        filters.append(RunHistory.status == status)
    # source=manual|sched（列表「来源」筛选，按 is_scheduled 标记；此前前端根本没传=假控件）
    if source in ("manual", "sched"):
        filters.append(RunHistory.is_scheduled == (1 if source == "sched" else 0))
    if schedule_id:
        filters.append(RunHistory.schedule_id == schedule_id)
    if device_id:
        filters.append(RunHistory.device_id == device_id)
    if from_time:
        filters.append(RunHistory.started_at >= from_time)
    if to_time:
        filters.append(RunHistory.started_at <= to_time)

    total_result = await db.execute(select(func.count(RunHistory.id)).where(*filters))
    total = total_result.scalar() or 0

    # SPEC §7.2-#4：JOIN 出 script_name/device_name（前端无字典可映射）
    query = (
        select(RunHistory, Script.name, Device.name)
        .join(Script, Script.id == RunHistory.script_id, isouter=True)
        .join(Device, Device.id == RunHistory.device_id, isouter=True)
        .where(*filters)
        .order_by(RunHistory.started_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    rows = result.all()

    items = []
    for rh, script_name, device_name in rows:
        rh.script_name = script_name
        rh.device_name = device_name
        if preview and rh.output and len(rh.output) > 2048:
            rh.output = rh.output[:2048]
        items.append(rh)

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
