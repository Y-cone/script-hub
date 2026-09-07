from fastapi import APIRouter, Depends, Query, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, or_, update
from sqlalchemy.ext.asyncio import AsyncSession
from ..database import get_db
from ..models.script import Script
from ..models.tag import ScriptTag, Tag
from ..models.run_history import RunHistory
from ..config import get_script_root
from ..schemas.script import ScriptOut, ScriptUpdate, ScriptListOut, ScanResult, TagsUpdate, MoveRequest
from ..schemas.tag import UploadResult
from ..services.scanner import scan_scripts, SUPPORTED_EXTENSIONS
from ..services.envcheck import check_environment
from ..services.parser import parse_script
from ..services.depscan import scan_deps
from pathlib import Path
from datetime import datetime
import json
import shutil
import io
import zipfile

router = APIRouter(prefix="/api/scripts", tags=["scripts"])

# 依赖清单敏感文件判定（信任边界：密钥/凭据/口令类禁止随传）
_SENSITIVE_EXT = {".env", ".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".crt", ".cer", ".p7b", ".pub"}
_SENSITIVE_NAME = {".env", ".git-credentials", ".netrc", ".hgrc", "id_rsa", "id_ed25519", ".htpasswd", "credentials", "secret", "secrets"}


def _is_sensitive_dep(rel: str) -> bool:
    """判断依赖路径是否为敏感文件（禁止随传）。"""
    name = rel.rsplit("/", 1)[-1].lower()
    if name in _SENSITIVE_NAME:
        return True
    if name.startswith(".env") or ".env." in name:
        return True
    return Path(rel).suffix.lower() in _SENSITIVE_EXT


def _dict_with_tags(script: Script, tag_names: list[str]) -> dict:
    """将 Script ORM 转 dict，附加标签名列表（供 ScriptOut 序列化）"""
    # 可用性：环境检测是否全达标（平台兼容 + 已配置的运行时要求）
    try:
        checks = check_environment(script)
        available = all(c["ok"] for c in checks)
    except Exception:
        available = None
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
        "env_requests": script.env_requests,
        "dependencies": script.dependencies,
        "available": available,
        "created_at": script.created_at,
        "updated_at": script.updated_at,
        "tags": tag_names,
    }
    return d


async def _load_tags(db: AsyncSession, script_ids: list[int]) -> dict[int, list[str]]:
    """按所属查询脚本标签名"""
    if not script_ids:
        return {}
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


@router.get("/{script_id}/dep-candidates")
async def list_dep_candidates(script_id: int, db: AsyncSession = Depends(get_db)):
    """返回脚本根目录下所有文件相对路径（供依赖清单多选候选）"""
    await db.execute(select(Script).where(Script.id == script_id))
    root = get_script_root()
    files = []
    if root.exists():
        for p in sorted(root.rglob("*")):
            if p.is_file():
                files.append(str(p.relative_to(root)))
    return {"files": files}


@router.get("/{script_id}/deps")
async def get_script_deps(script_id: int, db: AsyncSession = Depends(get_db)):
    """深度依赖分析：扫描脚本所在目录的依赖文件并检测安装状态（目录级归属）"""
    result = await db.execute(select(Script).where(Script.id == script_id))
    script = result.scalar_one_or_none()
    if not script:
        raise HTTPException(404, "Script not found")
    deps = await scan_deps(Path(script.path).parent)
    return {"script_id": script_id, "deps": deps, "dep_file": deps and True or False}


@router.get("/{script_id}/export")
async def export_script(script_id: int, db: AsyncSession = Depends(get_db)):
    """导出脚本为 ZIP（脚本文件 + manifest.json 配置）"""
    result = await db.execute(select(Script).where(Script.id == script_id))
    script = result.scalar_one_or_none()
    if not script:
        raise HTTPException(404, "Script not found")

    p = Path(script.path)
    if not p.exists():
        raise HTTPException(404, "Script file not found on disk")

    tag_map = await _load_tags(db, [script.id])
    manifest = {
        "script": {
            "name": script.name,
            "category": script.category,
            "relative_path": script.relative_path,
            "description": script.description,
            "parameters": json.loads(script.parameters or "[]"),
            "working_dir": script.working_dir,
            "env_vars": json.loads(script.env_vars) if script.env_vars else None,
            "dangerous": script.dangerous,
            "timeout": script.timeout,
            "env_requests": json.loads(script.env_requests) if script.env_requests else None,
        },
        "tags": tag_map.get(script.id, []),
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        # 脚本文件：保留相对路径便于导入还原目录结构
        zf.write(p, arcname=script.relative_path)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=script_{script.id}.zip"},
    )


@router.post("/import")
async def import_script(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """导入脚本 ZIP（脚本文件 + manifest.json 配置），还原目录结构与标签"""

    root = get_script_root()
    root.mkdir(parents=True, exist_ok=True)

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "ZIP 超过 10MB 限制")
    if not content.startswith(b"PK"):
        raise HTTPException(400, "不是有效的 ZIP 文件")

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = zf.namelist()
            # manifest
            manifest = None
            if "manifest.json" in names:
                manifest = json.loads(zf.read("manifest.json"))
            # 脚本文件：解压出支持扩展名的文件
            imported = []
            for n in names:
                if n.startswith("__MACOSX") or n.endswith("/"):
                    continue
                suffix = Path(n).suffix.lower()
                if suffix in SUPPORTED_EXTENSIONS:
                    data = zf.read(n)
                    # 防目录穿越：dest 必须落在 root 内
                    dest = (root / Path(n)).resolve()
                    if not dest.is_relative_to(root.resolve()):
                        continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                    imported.append(str(Path(n)))
    except zipfile.BadZipFile:
        raise HTTPException(400, "ZIP 文件损坏")

    # 扫描入库
    scan_result = await scan_scripts(db)

    imported_ids = []
    if imported:
        rels = [str(Path(x)).replace("\\", "/") for x in imported]
        r = await db.execute(select(Script).where(Script.relative_path.in_(rels)))
        imported_ids = [s.id for s in r.scalars().all()]

    # 恢复配置 + 标签
    if manifest and imported_ids:
        sc = manifest.get("script", {})
        first_id = imported_ids[0]
        # 找第一个导入的脚本 ID（manifest 描述的是主脚本）
        target = (await db.execute(select(Script).where(Script.id == first_id))).scalar_one_or_none()
        if target:
            for k in ("description", "working_dir", "env_vars", "dangerous", "timeout", "env_requests"):
                if k in sc:
                    v = sc[k]
                    if isinstance(v, (dict, list)):
                        v = json.dumps(v, ensure_ascii=False)
                    setattr(target, k, v)
            if sc.get("parameters"):
                target.parameters = json.dumps(sc["parameters"], ensure_ascii=False)
            target.source = "import"
        # 标签：按名匹配/建后关联
        tag_names = manifest.get("tags", [])
        if tag_names:
            new_ids = []
            for tname in tag_names:
                tr = await db.execute(select(Tag).where(Tag.name == tname))
                t = tr.scalar_one_or_none()
                if not t:
                    t = Tag(name=tname)
                    db.add(t)
                    await db.flush()
                new_ids.append(t.id)
            # 替换主脚本的标签
            old = await db.execute(select(ScriptTag).where(ScriptTag.script_id == first_id))
            for st in old.scalars().all():
                await db.delete(st)
            for tid in set(new_ids):
                db.add(ScriptTag(script_id=first_id, tag_id=tid))

    await db.commit()
    return {
        "message": f"导入 {len(imported)} 个脚本",
        "imported": imported,
        "scan": scan_result,
        "restored": bool(manifest),
    }


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

    data_dict = data.model_dump(exclude_unset=True)

    # 校验 env_requests 必须是合法 JSON 对象（dict of str）
    if "env_requests" in data_dict and data_dict.get("env_requests"):
        raw = data_dict["env_requests"]
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            raise HTTPException(400, "环境要求不是合法 JSON")
        if not isinstance(parsed, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()):
            raise HTTPException(400, "环境要求必须是 JSON 对象，如 {\"python\": \">=3.8\"}")

    for field, value in data_dict.items():
        setattr(script, field, value)

    # 校验 dependencies（随传文件清单）必须是 JSON 数组（文件相对脚本根路径），并过滤敏感文件（信任边界，禁止随传）
    if "dependencies" in data_dict and data_dict.get("dependencies"):
        raw = data_dict["dependencies"]
        try:
            deps = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            raise HTTPException(400, "随传文件清单不是合法 JSON")
        if not isinstance(deps, list):
            raise HTTPException(400, "随传文件清单必须是 JSON 数组，如 [\"utils.sh\", \"config/app.yaml\"]")
        root = get_script_root()
        cleaned = []
        for rel in deps:
            if not isinstance(rel, str) or not rel.strip():
                continue
            rel = rel.strip().replace("\\", "/")
            # 目录穿越防护：必须落在脚本根目录内
            abs = (root / rel).resolve()
            if not abs.is_relative_to(root.resolve()):
                raise HTTPException(400, f"非法随传路径（越出脚本根目录）: {rel}")
            # 敏感后缀过滤
            if _is_sensitive_dep(rel):
                raise HTTPException(400, f"敏感文件禁止随传（信任边界）: {rel}")
            cleaned.append(rel)
        setattr(script, "dependencies", json.dumps(cleaned, ensure_ascii=False))

    await db.commit()
    await db.refresh(script)
    tag_map = await _load_tags(db, [script.id])
    return _dict_with_tags(script, tag_map.get(script.id, []))


@router.delete("/{script_id}")
async def delete_script(script_id: int, db: AsyncSession = Depends(get_db)):
    """删除脚本：磁盘文件 + DB 记录（运行历史通过 ON DELETE SET NULL 保留，script_id 置空）"""
    result = await db.execute(select(Script).where(Script.id == script_id))
    script = result.scalar_one_or_none()
    if not script:
        raise HTTPException(404, "Script not found")

    # 删除磁盘文件（若存在）
    from pathlib import Path
    p = Path(script.path)
    if p.exists():
        try:
            p.unlink()
        except OSError:
            raise HTTPException(500, "删除磁盘文件失败")

    # 删除脚本记录；关联运行历史 script_id 由外键 SET NULL 自动置空保留
    # （手动置空兜底，避免依赖 PRAGMA 在部分连接未启用）
    await db.execute(update(RunHistory).where(RunHistory.script_id == script_id).values(script_id=None))
    await db.delete(script)
    await db.commit()
    return {"message": "脚本已删除，运行历史已保留", "id": script_id}


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


@router.put("/{script_id}/content")
async def save_script_content(
    script_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """保存脚本内容：写入磁盘文件 + 触发重扫更新 DB 元数据"""
    result = await db.execute(select(Script).where(Script.id == script_id))
    script = result.scalar_one_or_none()
    if not script:
        raise HTTPException(404, "Script not found")

    content = payload.get("content", "")
    p = Path(script.path)
    try:
        p.write_text(content, encoding="utf-8")
    except OSError as e:
        raise HTTPException(500, f"写入文件失败: {e}")

    # 重扫更新 DB 中的元数据（名称/路径/更新时间等）
    await scan_scripts(db)
    return {"message": "已保存", "language": script.category}


@router.get("/{script_id}/env-check")
async def env_check(script_id: int, db: AsyncSession = Depends(get_db)):
    """手动触发某脚本的环境检测（平台兼容 + 运行时版本）"""
    result = await db.execute(select(Script).where(Script.id == script_id))
    script = result.scalar_one_or_none()
    if not script:
        raise HTTPException(404, "Script not found")
    checks = check_environment(script)
    return {
        "script_id": script_id,
        "checks": checks,
        "unmet": [c for c in checks if not c["ok"]],
        "all_ok": all(c["ok"] for c in checks),
    }


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