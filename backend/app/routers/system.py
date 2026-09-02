from fastapi import APIRouter
from ..services.envcheck import system_info

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/info")
async def get_system_info():
    """本机设备信息 + 运行时版本（只读）"""
    return system_info()