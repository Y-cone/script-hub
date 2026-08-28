from pathlib import Path
from datetime import datetime
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from ..models.script import Script
from ..models.run_history import RunHistory
from ..config import get_script_root

EXTENSION_MAP = {
    ".py": "python",
    ".sh": "shell",
    ".bat": "bat",
    ".ps1": "powershell",
}

SUPPORTED_EXTENSIONS = set(EXTENSION_MAP.keys())


async def scan_scripts(db: AsyncSession) -> dict:
    root = get_script_root()
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
        return {"added": 0, "updated": 0, "removed": 0, "total": 0}

    # Collect current files on disk
    disk_files: dict[str, Path] = {}
    for f in root.rglob("*"):
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS:
            disk_files[str(f)] = f

    # Get existing scripts from DB
    result = await db.execute(select(Script))
    existing = {s.path: s for s in result.scalars().all()}

    added = updated = removed = 0

    # Add or update
    for abs_path, filepath in disk_files.items():
        rel_path = str(filepath.relative_to(root))
        ext = filepath.suffix.lower()
        category = EXTENSION_MAP[ext]
        now = datetime.utcnow()

        if abs_path in existing:
            script = existing[abs_path]
            # 仅当磁盘文件 mtime 比库记录更新时才视为"更新"（内容可能已变）
            # 统一为 naive UTC dataetime 比较，避免 naive datetime .timestamp() 的本地时区陷阱
            file_mtime_utc = datetime.utcfromtimestamp(filepath.stat().st_mtime)
            store_mtime = script.updated_at or datetime.fromtimestamp(0)
            if file_mtime_utc > store_mtime:
                script.name = filepath.name
                script.relative_path = rel_path
                script.extension = ext
                script.category = category
                script.updated_at = now
                updated += 1
        else:
            script = Script(
                name=filepath.name,
                path=abs_path,
                relative_path=rel_path,
                extension=ext,
                category=category,
                created_at=now,
                updated_at=now,
            )
            db.add(script)
            added += 1

    # Remove scripts whose files no longer exist
    for abs_path, script in existing.items():
        if abs_path not in disk_files:
            # 先删关联运行历史，避免外键约束失败
            await db.execute(delete(RunHistory).where(RunHistory.script_id == script.id))
            await db.delete(script)
            removed += 1

    await db.commit()

    total_result = await db.execute(select(func.count(Script.id)))
    total = total_result.scalar() or 0

    return {"added": added, "updated": updated, "removed": removed, "total": total}
