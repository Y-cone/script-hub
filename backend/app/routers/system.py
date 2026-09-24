from fastapi import APIRouter
from ..services.envcheck import system_info
from ..config import DATA_DIR, load_config

router = APIRouter(prefix="/api/system", tags=["system"])


def _collect() -> dict:
    """本机信息 + 数据目录 + 只读标记 + 探测时间（/info 与 /probe 共用，同构响应体）"""
    info = system_info()
    try:
        info["data_dir"] = str(DATA_DIR)
        info["scripts_root"] = str(load_config().get("script_root_dir") or "")
    except Exception:
        info["data_dir"] = ""
        info["scripts_root"] = ""
    # v2.14：置灰唯一依据 = 外部覆盖变量是否存在（前端不许自己猜环境变量）
    import os
    info["readonly"] = {
        "data_dir": bool(os.environ.get("SCRIPTHUB_DATA_DIR")),
        "scripts_root": bool(os.environ.get("SCRIPTHUB_SCRIPTS_ROOT")),
    }
    from datetime import datetime
    info["probed_at"] = datetime.now().isoformat()
    return info


@router.get("/info")
async def get_system_info():
    """本机设备信息 + 运行时版本 + V5-G 展示字段（只读）

    SPEC §7.2-#5：data_dir / scripts_root / probed_at（本机信息页概览卡）。
    """
    return _collect()


@router.post("/probe")
async def probe_system():
    """现场重新探测一次本机信息（复用同一采集函数，响应体与 /info 同构）"""
    return _collect()
