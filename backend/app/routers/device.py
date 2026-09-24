from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
import time
from ..database import get_db
from ..models.device import Device
from ..models.schedule import Schedule
from ..schemas.device import DeviceOut, DeviceCreate, DeviceUpdate, TestConnResult
from ..services.secret_store import set_secret, delete_secret
from ..services.ssh_service import pool, build_client, remote_probe, _service_name
from ..services.ssh_service import cache_probe, fresh_entry

router = APIRouter(prefix="/api/devices", tags=["devices"])

# SPEC §7.2-#2：设备展示字段 = 最近一次 test/probe 的内存缓存（带 TTL）。
# 列表接口绝不主动 SSH 探测（3 台离线设备 = 3 次 SSH 握手超时），未探测过显示「未探测」。
# 批次 AM：缓存本体与 cache_probe/fresh_entry 搬到了 services/ssh_service —— 读穿透的
# detect_has_bash 也要读同一份结论，留在 router 里就只能让 service 反向 import router 的
# 私有全局。新鲜度判定也收进 fresh_entry（本文件与读穿透两处别再各写一遍 TTL 比较）。


async def _sched_counts(db: AsyncSession) -> dict[int, tuple[int, int]]:
    """device_id → (任务总数, 下放任务数)（SPEC §7.2-#2 后两项）"""
    from sqlalchemy import Integer, cast
    rows = (await db.execute(
        select(Schedule.device_id, func.count(Schedule.id),
               func.sum(cast(Schedule.exec_location == "device", Integer)))
        .where(Schedule.device_id.isnot(None))
        .group_by(Schedule.device_id)
    )).all()
    return {r[0]: (r[1], int(r[2] or 0)) for r in rows}


@router.get("", response_model=list[DeviceOut])
async def list_devices(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).order_by(Device.id))
    devs = list(result.scalars().all())
    counts = await _sched_counts(db)
    out: list[DeviceOut] = []
    for d in devs:
        extra: dict = {}
        c = fresh_entry(d.id)
        if c:
            extra = {
                "online": bool(c["ok"]),
                "latency_ms": c.get("latency_ms"),
                "os_info": c.get("os_info"),
                "last_error": None if c["ok"] else c.get("error"),
                # 批次 AK①：未探测过/缓存过期 → 这里进不来 → runtimes 保持 None（「无结论」）
                "runtimes": c.get("runtimes"),
                # 批次 AO：探测到的平台原文（win32/unix/windows）。设备上下文下的展示与
                # 环境检测判定都要它（环境检测在后端直接读缓存，这里给本机信息页复用同一结论）。
                "platform": c.get("platform"),
                # 批次 AQ：结论年龄（monotonic 差值，只算「过了多久」；见 DeviceOut 注释）
                "probed_age_ms": int((time.monotonic() - c["ts"]) * 1000),
            }
        # 未探测过 → 字段保持 None，前端显示「未探测」
        total, delegated = counts.get(d.id, (0, 0))
        out.append(DeviceOut(
            **{c.name: getattr(d, c.name) for c in d.__table__.columns},
            schedule_count=total, delegated_count=delegated, **extra,
        ))
    return out


@router.post("", response_model=DeviceOut)
async def create_device(data: DeviceCreate, db: AsyncSession = Depends(get_db)):
    devices = (await db.execute(select(Device))).scalars().all()
    if any(d.host == data.host and d.username == data.username for d in devices):
        raise HTTPException(400, "该主机/用户组合已存在")

    dev = Device(
        name=data.name,
        type=data.type,
        host=data.host,
        port=data.port,
        auth_type=data.auth_type,
        username=data.username,
        auth_ref="",  # auth_ref 占位；凭据实际存 keyring
    )
    db.add(dev)
    await db.commit()
    await db.refresh(dev)

    # 存凭据（dev.id 已知后固定 service 名）
    secret = data.password if data.auth_type == "password" else data.private_key
    if secret:
        set_secret(_service_name(dev.id), data.username, secret)
    return dev


@router.put("/{device_id}", response_model=DeviceOut)
async def update_device(device_id: int, data: DeviceUpdate, db: AsyncSession = Depends(get_db)):
    dev = (await db.execute(select(Device).where(Device.id == device_id))).scalar_one_or_none()
    if not dev:
        raise HTTPException(404, "Device not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        if field in ("password", "private_key"):
            if value is not None:  # 更新凭据
                set_secret(_service_name(dev.id), dev.username, value)
            continue
        setattr(dev, field, value)
    await db.commit()
    await db.refresh(dev)
    await pool.invalidate()  # 凭据变化 → 失效已缓存连接
    return dev


@router.delete("/{device_id}")
async def delete_device(device_id: int, db: AsyncSession = Depends(get_db)):
    dev = (await db.execute(select(Device).where(Device.id == device_id))).scalar_one_or_none()
    if not dev:
        raise HTTPException(404, "Device not found")
    await pool.close(device_id)
    delete_secret(_service_name(dev.id), dev.username)
    await db.delete(dev)
    await db.commit()
    return {"message": "已删除"}


@router.post("/{device_id}/test", response_model=TestConnResult)
async def test_device(device_id: int, db: AsyncSession = Depends(get_db)):
    """测试 SSH 连接 + 远端环境探测（复用设备凭据）"""
    dev = (await db.execute(select(Device).where(Device.id == device_id))).scalar_one_or_none()
    if not dev:
        raise HTTPException(404, "Device not found")
    try:
        t0 = time.monotonic()
        client = build_client(dev)
        client.close()
        latency_ms = int((time.monotonic() - t0) * 1000)  # SPEC §7.2-#6：握手耗时
    except Exception as e:
        hint = "请检查防火墙/端口/凭据" if ("timed out" in str(e).lower() or "refused" in str(e).lower()) else ""
        cache_probe(device_id, ok=False, error=str(e))  # 失败也入缓存（卡上显示失败原因）
        return TestConnResult(
            ok=False,
            message=f"连接失败: {str(e)}{('；' + hint) if hint else ''}",
        )
    # 平台探测（remote_probe 内部吞掉异常 → 用 ok 字段判成败；异常与 ok=False 归同一条降级路径）
    try:
        probe = await remote_probe(dev)
    except Exception as e:
        probe = {"ok": False, "error": str(e), "platform": "unix", "os_info": "", "runtimes": []}
    if not probe.get("ok", True):
        # 批次 AL：此刻 runtimes 里全是探测失败的条目（installed=false、version="探测失败: …"），
        # **不是结论**，绝不能入缓存（否则设备卡会显示「确定没装 bash」）。设备本身可达
        # （握手已成功）→ 上面缓存的 runtimes/os_info 不作废，只刷新 latency。
        cache_probe(device_id, ok=True, latency_ms=latency_ms)
        return TestConnResult(ok=True, message=f"连接成功（平台探测失败: {probe.get('error')}）",
                              latency_ms=latency_ms)
    cache_probe(device_id, ok=True, os_info=probe["os_info"], latency_ms=latency_ms,
                runtimes=probe["runtimes"], platform=probe["platform"])
    # 批次 AK①：runtimes 一并入缓存，供列表接口与前端判 .sh
    # 批次 AO：platform 同上（设备上下文环境检测的判据来源）
    return TestConnResult(
        ok=True,
        message="连接成功",
        platform=probe["platform"],
        os_info=probe["os_info"],
        latency_ms=latency_ms,
    )


@router.get("/{device_id}/probe")
async def probe_device(device_id: int, db: AsyncSession = Depends(get_db)):
    """获取远端设备信息（platform/os/运行时），供本机信息页在设备上下文下展示"""
    dev = (await db.execute(select(Device).where(Device.id == device_id))).scalar_one_or_none()
    if not dev:
        raise HTTPException(404, "Device not found")
    # G1-A：探测失败要给出可读原因（否则全局异常处理器把一切压成「服务器内部错误」，
    # 本机信息页只能显示无信息量的提示）
    try:
        probe = await remote_probe(dev)
    except Exception as e:
        cache_probe(device_id, ok=False, error=str(e))  # 批次 AL②：与 /test 同口径，卡上显示失败原因
        raise HTTPException(502, str(e)) from e
    # remote_probe 内部会吞掉连接/认证异常 → 这里把「首探失败」升级成真正的错误响应，
    # 否则不可达设备会以 200 + 一屏「探测失败: …」字段返回，前端无从判定
    if not probe.get("ok", True):
        err = str(probe.get("error") or "无法连接目标设备")
        cache_probe(device_id, ok=False, error=err)   # 够不着 = 旧结论作废（runtimes 一并清掉）
        raise HTTPException(502, err)
    # 批次 AL②：探测结果写同一份 _probe_cache（本机信息页切设备就会调本端点 → 列表接口随之可见）。
    # 不传 latency_ms（本端点不测握手耗时）→ 合并语义下保留上次 test 的延时，不会被冲成 None。
    # 响应形状保持不变（{name,type,host,platform,os,port,runtimes}），缓存只是旁路副作用。
    cache_probe(device_id, ok=True, os_info=probe["os_info"], runtimes=probe["runtimes"],
                platform=probe["platform"])
    return {
        "name": dev.name,
        "type": dev.type,
        "host": dev.host,
        "platform": probe["platform"],
        "os": probe["os_info"],
        "port": dev.port,
        "runtimes": probe["runtimes"],
    }