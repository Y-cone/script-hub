"""设置面板后端（SPEC §2.9 / §7.1 / §7.8）。

保存只写配置（settings.json），不迁移、不重启、不热切换；
DB 连接与脚本扫描根都在启动时打开 → 一律 restart_required=true。
"""
import os
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from ..config import CONFIG_PATH, DATA_DIR, load_config, save_config
from ..datamigrate import migrate_if_needed

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsBody(BaseModel):
    data_dir: str | None = None
    scripts_root: str | None = None
    migrate: bool = False


class ValidateBody(BaseModel):
    data_dir: str | None = None
    scripts_root: str | None = None


def _dir_writeable(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".scripthub_write_probe"
        probe.write_text("")
        probe.unlink()
        return True
    except OSError:
        return False


def _validate_field(raw: str, kind: str) -> dict:
    """kind: "data_dir" | "scripts_root"。逐字段独立，不抛异常。"""
    out: dict = {"exists": False, "writable": False, "empty": True, "error": None}
    try:
        p = Path(raw).expanduser()
    except (OSError, ValueError) as e:
        out["error"] = f"路径无效: {e}"
        return out
    if not raw.strip():
        out["error"] = "路径不能为空"
        return out
    if not p.exists():
        # 沿父链找最近一个已存在祖先判断可创建性（多级不存在不算 err）
        anc = p.parent
        while not anc.exists() and anc != anc.parent:
            anc = anc.parent
        if anc.is_dir() and _dir_writeable(anc):
            out["error"] = None  # 不存在但可创建 → 非阻断
        else:
            out["error"] = "目录不存在，且无法创建（父目录不可写）"
        return out
    if not p.is_dir():
        out["error"] = "路径存在但不是目录"
        return out
    out["exists"] = True
    out["writable"] = _dir_writeable(p)
    if kind == "data_dir":
        if (p / "scripthub.db").exists():
            # v2.14：只统计本体（db + runs/），不 rglob 全树——前端 300ms 防抖逐键校验
            db = (p / "scripthub.db").stat().st_size
            runs = sum(f.stat().st_size for f in (p / "runs").rglob("*") if f.is_file()) \
                if (p / "runs").is_dir() else 0
            out["size_mb"] = round((db + runs) / 1048576, 1)
        out["empty"] = not (p / "scripthub.db").exists()
    else:
        out["script_count"] = sum(1 for f in p.rglob("*") if f.is_file() and f.suffix in (".py", ".sh", ".ps1", ".bat", ".cmd", ".js", ".ts"))
        out["dir_count"] = sum(1 for f in p.iterdir() if f.is_dir())
        out["empty"] = not any(p.iterdir())
    if not out["writable"]:
        out["error"] = "目录存在但不可写"
    return out


@router.post("/validate")
async def validate_settings(body: ValidateBody):
    """行内校验：逐字段独立，一个字段非法不阻断另一字段。"""
    result: dict = {}
    if body.data_dir is not None:
        result["data_dir"] = _validate_field(body.data_dir, "data_dir")
    if body.scripts_root is not None:
        result["scripts_root"] = _validate_field(body.scripts_root, "scripts_root")
    return result


@router.put("")
async def put_settings(body: SettingsBody):
    cfg = load_config()
    warnings: list[str] = []
    migrated = False

    data_dir = body.data_dir
    scripts_root = body.scripts_root
    if data_dir is not None:
        v = _validate_field(data_dir, "data_dir")
        # 不存在但可创建（error=None）放行——迁移到新目录是合法场景
        if v["error"]:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=v["error"])
    if scripts_root is not None:
        v = _validate_field(scripts_root, "scripts_root")
        if v["error"]:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=v["error"])

    if data_dir is not None and body.migrate:
        src, dst = DATA_DIR, Path(data_dir).expanduser()
        if src != dst:
            if (dst / "scripthub.db").exists():
                warnings.append("目标目录已有 scripthub.db，未迁移")
            else:
                migrated = migrate_if_needed(src, dst)
                if not migrated:
                    warnings.append("无可迁移数据（源目录无 scripthub.db）")

    if data_dir is not None:
        if os.environ.get("SCRIPTHUB_DATA_DIR"):
            # v2.14：被 env 遮蔽的字段不得写盘（否则取消 env 后会静默用上脏值）
            warnings.append("SCRIPTHUB_DATA_DIR 已设置，data_dir 以环境变量为准，未写入")
        else:
            cfg["data_dir"] = data_dir
    if scripts_root is not None:
        if os.environ.get("SCRIPTHUB_SCRIPTS_ROOT"):
            warnings.append("SCRIPTHUB_SCRIPTS_ROOT 已设置，scripts_root 以环境变量为准，未写入")
        else:
            cfg["script_root_dir"] = scripts_root
    save_config(cfg)

    saved = load_config()
    return {
        "ok": True,
        "data_dir": str(DATA_DIR),
        "scripts_root": str(saved.get("script_root_dir") or ""),
        "restart_required": True,
        "migrated": migrated,
        "warnings": warnings,
    }
