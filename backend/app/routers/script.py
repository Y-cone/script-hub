from fastapi import APIRouter, Depends, Query, HTTPException, UploadFile, File
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from ..database import get_db
from ..models.script import Script
from ..models.tag import ScriptTag
from ..config import get_script_root
from ..schemas.script import ScriptOut, ScriptUpdate, ScriptListOut, ScanResult, TagsUpdate, MoveRequest
from ..schemas.tag import UploadResult
from ..services.scanner import scan_scripts, SUPPORTED_EXTENSIONS
from ..services.parser import parse_script
from pathlib import Path
from datetime import datetime
import json
import shutil

router = APIRouter(prefix="/api/scripts", tags=["scripts"])


def _dict_with_tags(script: Script, tag_names: list[str]) -> dict:
    """将 Script ORM 转 dict，附加标签名列表（供 ScriptOut 序列化）"""
    d = {
        "id": script.id,
        "name": script.name,
        "path": script.path,
        "relative_path": script.relative_path,
        "extension": script.extension,
        "category": script.category,
        "description": script.description,
        "parameters": script.parameters,
        "working_dir": script.working_dir,
        "env_vars": script.env_vars,
        "dangerous": script.dangerous,
        "timeout": script.timeout,
        "source": script.source,
        "created_at": script.created_at,
        "updated_at": script.updated_at,
        "tags": tag_names,
    }
    return d


async def _load_tags(db: AsyncSession, script_ids: list[int]) -> dict[int, list[str]]:
    """按所属查询脚本标签名"""
    if not script_ids:
        return {}
    from ..models.tag import Tag
    result = await db.execute(
        select(ScriptTag.script_id, Tag.name)
        .join(Tag, Tag.id == ScriptTag.tag_id)
        .where(ScriptTag.script_id.in_(script_ids))
    )
    mapping: dict[int, list[str]] = {sid: [] for sid in script_ids}
    for sid, name in result.all():
        mapping[sid].append(name)
    return mapping


def _serialize_list(items, tag_map: dict[int, list[str]]) -> list[dict]:
    return [_dict_with_tags(s, tag_map.get(s.id, [])) for s in items]


@router.get("", response_model=ScriptListOut)
async def list_scripts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str = Query("", description="按文件名搜索"),
    directory: str = Query("", description="按目录筛选"),
    category: str = Query("", description="按类型筛选"),
    tag_ids: str = Query("", description="按标签ID筛选(逗号分隔，AND语义)"),
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

    if category:
        query = query.where(Script.category == category)
        count_query = count_query.where(Script.category == category)

    # 标签 AND 过滤：脚本须同时具备所有 tag_id
    if tag_ids:
        ids = [int(t.strip()) for t in tag_ids.split(",") if t.strip().isdigit()]
        inner = select(ScriptTag.script_id).where(ScriptTag.tag_id.in_(ids)).group_by(ScriptTag.script_id).having(func.count(ScriptTag.tag_id) == len(ids))
        script_ids = [r[0] for r in (await db.execute(inner)).all()]
        if not script_ids:
            script_ids = [-1]  # 无匹配，返回空
        query = query.where(Script.id.in_(script_ids))
        count_query = count_query.where(Script.id.in_(script_ids))

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(Script.relative_path).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = list(result.scalars().all())

    tag_map = await _load_tags(db, [s.id for s in items])
    return ScriptListOut(items=_serialize_list(items, tag_map), total=total, page=page, page_size=page_size)


@router.get("/dirs")
async def list_script_dirs(db: AsyncSession = Depends(get_db)):
    """返回脚本根目录下已有的子目录列表（相对路径）"""
    root = get_script_root()
    dirs = set()
    if root.exists():
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
                rel_dir = p.parent.relative_to(root)
                if str(rel_dir) != ".":
                    dirs.add(str(rel_dir))
    return {"directories": sorted(dirs)}


@router.get("/{script_id}", response_model=ScriptOut)
async def get_script(script_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Script).where(Script.id == script_id))
    script = result.scalar_one_or_none()
    if not script:
        raise HTTPException(404, "Script not found")
    tag_map = await _load_tags(db, [script.id])
    return _dict_with_tags(script, tag_map.get(script.id, []))


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
    tag_map = await _load_tags(db, [script.id])
    return _dict_with_tags(script, tag_map.get(script.id, []))


@router.post("/{script_id}/move", response_model=ScriptOut)
async def move_script(script_id: int, data: MoveRequest, db: AsyncSession = Depends(get_db)):
    """将脚本移动到脚本根目录下的另一子目录"""
    result = await db.execute(select(Script).where(Script.id == script_id))
    script = result.scalar_one_or_none()
    if not script:
        raise HTTPException(404, "Script not found")

    root = get_script_root()
    # 目标目录：相对脚本根目录的子目录（可为空=根目录）
    target = (root / data.directory.strip()).resolve()
    if not target.is_relative_to(root.resolve()):
        raise HTTPException(400, "非法目录路径")
    target.mkdir(parents=True, exist_ok=True)

    src = Path(script.path)
    if not src.exists():
        raise HTTPException(404, "源文件不存在")

    # 目标文件（防重名：存在则加 (1) 后缀）
    dest = target / src.name
    if dest.exists():
        stem, suffix = src.stem, src.suffix
        i = 1
        while (target / f"{stem}({i}){suffix}").exists():
            i += 1
        dest = target / f"{stem}({i}){suffix}"

    shutil.move(str(src), str(dest))

    script.path = str(dest)
    script.relative_path = str(dest.relative_to(root))
    script.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(script)
    tag_map = await _load_tags(db, [script.id])
    return _dict_with_tags(script, tag_map.get(script.id, []))


@router.post("/scan", response_model=ScanResult)
async def trigger_scan(db: AsyncSession = Depends(get_db)):
    return await scan_scripts(db)


@router.post("/upload", response_model=UploadResult)
async def upload_script(
    file: UploadFile = File(...),
    subdir: str = Query("", description="上传到的子目录，相对根目录"),
    db: AsyncSession = Depends(get_db),
):
    """上传脚本文件到根目录，落盘后自动扫描"""
    # 扩展名白名单
    ext = Path(file.filename or "").suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(400, f"不支持的文件类型: {ext}，仅支持 {'/'.join(sorted(SUPPORTED_EXTENSIONS))}")

    root = get_script_root()
    root.mkdir(parents=True, exist_ok=True)

    # 目标目录 + 防目录穿越（只允许根目录内的子目录）
    if subdir:
        target = (root / subdir).resolve()
        if not target.resolve().is_relative_to(root.resolve()):
            raise HTTPException(400, "非法子目录路径")
        target.mkdir(parents=True, exist_ok=True)
    else:
        target = root

    # 安全文件名（只取 basename，防路径注入）
    safe_name = Path(file.filename or "upload").name
    dest = (target / safe_name).resolve()
    if not dest.resolve().is_relative_to(root.resolve()):
        raise HTTPException(400, "非法文件名")

    content = await file.read()
    # 大小限制 10MB
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "文件超过 10MB 限制")

    dest.write_bytes(content)

    # 自动扫描
    result = await scan_scripts(db)
    return UploadResult(**result, message=f"已上传 {safe_name} 并扫描")

@router.get("/{script_id}/tags", response_model=list[str])
async def get_script_tags(script_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Script).where(Script.id == script_id))
    script = result.scalar_one_or_none()
    if not script:
        raise HTTPException(404, "Script not found")
    tag_map = await _load_tags(db, [script.id])
    return tag_map.get(script.id, [])


@router.put("/{script_id}/tags")
async def set_script_tags(script_id: int, data: TagsUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Script).where(Script.id == script_id))
    script = result.scalar_one_or_none()
    if not script:
        raise HTTPException(404, "Script not found")

    # 删除旧的标签关联
    old = await db.execute(select(ScriptTag).where(ScriptTag.script_id == script_id))
    for st in old.scalars().all():
        await db.delete(st)

    # 添加新的
    for tag_id in set(data.tag_ids):
        db.add(ScriptTag(script_id=script_id, tag_id=tag_id))

    await db.commit()
    tag_map = await _load_tags(db, [script.id])
    return {"script_id": script_id, "tags": tag_map.get(script.id, [])}


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