from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from ..database import get_db
from ..models.script import Script
from ..schemas.script import ScriptOut, ScriptUpdate, ScriptListOut, ScanResult
from ..services.scanner import scan_scripts
from ..services.parser import parse_script
import json

router = APIRouter(prefix="/api/scripts", tags=["scripts"])


@router.get("", response_model=ScriptListOut)
async def list_scripts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str = Query("", description="按文件名搜索"),
    directory: str = Query("", description="按目录筛选"),
    db: AsyncSession = Depends(get_db),
):
    query = select(Script)
    count_query = select(func.count(Script.id))

    if search:
        like = f"%{search}%"
        cond = or_(Script.name.ilike(like), Script.relative_path.ilike(like))
        query = query.where(cond)
        count_query = count_query.where(cond)

    if directory:
        query = query.where(Script.relative_path.like(f"{directory}%"))
        count_query = count_query.where(Script.relative_path.like(f"{directory}%"))

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(Script.relative_path).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = result.scalars().all()

    return ScriptListOut(items=items, total=total, page=page, page_size=page_size)


@router.get("/{script_id}", response_model=ScriptOut)
async def get_script(script_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Script).where(Script.id == script_id))
    script = result.scalar_one_or_none()
    if not script:
        raise HTTPException(404, "Script not found")
    return script


@router.put("/{script_id}", response_model=ScriptOut)
async def update_script(script_id: int, data: ScriptUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Script).where(Script.id == script_id))
    script = result.scalar_one_or_none()
    if not script:
        raise HTTPException(404, "Script not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(script, field, value)

    await db.commit()
    await db.refresh(script)
    return script


@router.post("/scan", response_model=ScanResult)
async def trigger_scan(db: AsyncSession = Depends(get_db)):
    return await scan_scripts(db)


@router.get("/{script_id}/content")
async def get_script_content(script_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Script).where(Script.id == script_id))
    script = result.scalar_one_or_none()
    if not script:
        raise HTTPException(404, "Script not found")

    from pathlib import Path
    p = Path(script.path)
    if not p.exists():
        raise HTTPException(404, "Script file not found on disk")

    try:
        content = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = p.read_text(encoding="latin-1")

    return {"content": content, "language": script.category}


@router.post("/{script_id}/parse")
async def parse_script_params(script_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Script).where(Script.id == script_id))
    script = result.scalar_one_or_none()
    if not script:
        raise HTTPException(404, "Script not found")

    from pathlib import Path
    p = Path(script.path)
    if not p.exists():
        raise HTTPException(404, "Script file not found on disk")

    params = parse_script(p, script.category)
    script.parameters = json.dumps(params, ensure_ascii=False)
    await db.commit()
    await db.refresh(script)

    return {"id": script.id, "parameters": params}
