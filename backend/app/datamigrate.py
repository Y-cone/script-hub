"""数据目录一次性迁移（PRD-V5 4.G / Phase V5-A）。

场景：桌面形态首启时，SCRIPTHUB_DATA_DIR 指向用户数据目录，而旧仓库 data/ 里有
历史数据（SQLite/config/scripts/runs）。本模块把旧目录**复制**（不删除源）到新目录，
并修复持久化在库/配置中的**绝对路径**（仅复制目录不够，否则扫描/执行/日志全断）。

幂等：新目录已存在 scripthub.db 或标记文件 → 跳过（二次启动不重复迁移）。
"""
import json
import logging
import shutil
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_MIGRATED_MARKER = ".migrated_from"


def migrate_if_needed(old_dir: Path, new_dir: Path) -> bool:
    """把 old_dir（仓库 data/）迁移到 new_dir（用户数据目录）。返回是否执行了迁移。

    - new_dir 空（无 db）→ 全量复制 + 路径修复
    - new_dir 已有 db：带 marker → 跳过（迁过）；不带 marker（用户手动复制/半迁移）
      → 只补做路径修复 + 补缺文件（不覆盖已有 db）
    """
    old_dir, new_dir = Path(old_dir), Path(new_dir)
    if new_dir == old_dir:
        return False
    marker = new_dir / _MIGRATED_MARKER
    new_db = new_dir / "scripthub.db"
    old_db = old_dir / "scripthub.db"
    if marker.exists():
        return False  # 已迁移过
    if not old_db.exists() and not new_db.exists():
        return False  # 两边都无库，无事可做

    old_scripts_root = str(old_dir / "scripts")
    new_scripts_root = str(new_dir / "scripts")
    logger.info(f"数据迁移: {old_dir} -> {new_dir}")
    new_dir.mkdir(parents=True, exist_ok=True)

    if not new_db.exists() and old_db.exists():
        # 1) 全量复制（db/config/scripts/runs）
        for item in ("scripthub.db", "config.json"):
            src = old_dir / item
            if src.exists() and not (new_dir / item).exists():
                shutil.copy2(src, new_dir / item)
        for sub in ("scripts", "runs"):
            src, dst = old_dir / sub, new_dir / sub
            if src.exists():
                shutil.copytree(src, dst, dirs_exist_ok=True)

    # 2) 路径修复三步（绝对路径 → 新目录）——无论复制与否都执行
    _fix_config(new_dir, old_scripts_root, new_scripts_root)
    if new_db.exists():
        _fix_db_paths(new_db, old_scripts_root, new_scripts_root)

    marker.write_text(str(old_dir))
    logger.info("数据迁移完成")
    return True


def _fix_config(new_dir: Path, old_root: str, new_root: str):
    """修复 config.json 的 script_root_dir。"""
    cfg_path = new_dir / "config.json"
    try:
        cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        root = cfg.get("script_root_dir", "")
        if old_root in root:
            cfg["script_root_dir"] = root.replace(old_root, new_root)
        else:
            cfg["script_root_dir"] = new_root  # 无记录或指向别处 → 指向新目录
        cfg_path.write_text(json.dumps(cfg, indent=2))
    except Exception as e:
        logger.warning(f"config.json 路径修复失败（保留原样）: {e}")


def _fix_db_paths(db_path: Path, old_root: str, new_root: str):
    """修复 SQLite 内持久化的绝对路径（scripts.path / run_history.output_file）。"""
    if old_root not in ("", "/") and old_root == new_root:
        return
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("UPDATE scripts SET path = replace(path, ?, ?)", (old_root, new_root))
        cur.execute(
            "UPDATE run_history SET output_file = replace(output_file, ?, ?) "
            "WHERE output_file IS NOT NULL",
            (str(Path(old_root).parent), str(Path(new_root).parent)),
        )
        conn.commit()
        logger.info(f"DB 路径修复: scripts.path/output_file -> {new_root}")
    except Exception as e:
        logger.warning(f"DB 路径修复失败（保留原样，可手动处理）: {e}")
    finally:
        conn.close()
