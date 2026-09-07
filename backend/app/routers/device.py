from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..database import get_db
from ..models.device import Device
from ..schemas.device import DeviceOut, DeviceCreate, DeviceUpdate, TestConnResult
from ..services.secret_store import set_secret, delete_secret
from ..services.ssh_service import pool, build_client, remote_probe, _service_name

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.get("", response_model=list[DeviceOut])
async def list_devices(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).order_by(Device.id))
    return list(result.scalars().all())


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
        client = build_client(dev)
        client.close()
    except Exception as e:
        hint = "请检查防火墙/端口/凭据" if ("timed out" in str(e).lower() or "refused" in str(e).lower()) else ""
        return TestConnResult(
            ok=False,
            message=f"连接失败: {str(e)}{('；' + hint) if hint else ''}",
        )
    # 平台探测
    try:
        probe = await remote_probe(dev)
        return TestConnResult(
            ok=True,
            message="连接成功",
            platform=probe["platform"],
            os_info=probe["os_info"],
        )
    except Exception as e:
        return TestConnResult(ok=True, message=f"连接成功（平台探测失败: {e}）")


@router.get("/{device_id}/probe")
async def probe_device(device_id: int, db: AsyncSession = Depends(get_db)):
    """获取远端设备信息（platform/os/运行时），供本机信息页在设备上下文下展示"""
    dev = (await db.execute(select(Device).where(Device.id == device_id))).scalar_one_or_none()
    if not dev:
        raise HTTPException(404, "Device not found")
    probe = await remote_probe(dev)
    return {
        "name": dev.name,
        "type": dev.type,
        "host": dev.host,
        "platform": probe["platform"],
        "os": probe["os_info"],
        "port": dev.port,
        "runtimes": probe["runtimes"],
    }