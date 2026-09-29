from fastapi import APIRouter, Depends, Query, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, or_, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from ..database import get_db
from ..models.script import Script
from ..models.device import Device
from ..models.tag import ScriptTag, Tag
from ..models.run_history import RunHistory
from ..config import get_script_root
from ..schemas.script import ScriptOut, ScriptUpdate, ScriptListOut, ScanResult, TagsUpdate, MoveRequest
from ..schemas.brief import LastRunBrief
from ..schemas.tag import UploadResult
from ..services.scanner import scan_scripts, SUPPORTED_EXTENSIONS
from ..services.envcheck import check_environment
from ..services.ssh_service import fresh_entry
from ..services.parser import parse_script
from ..services.script_text import (   # noqa: F401 — probe_script_text/_encoding_label 亦为本模块导出名
    _encoding_candidates, _encoding_label, preserve_eol, probe_script_text)
from ..services.executor import _win_ansi_codec, encode_script_bytes
from ..services.depscan import scan_deps
from pathlib import Path
from typing import Optional
from datetime import datetime
import codecs
import json
import logging
import os
import shutil
import sys
import tempfile
import io
import zipfile

router = APIRouter(prefix="/api/scripts", tags=["scripts"])

logger = logging.getLogger(__name__)

# 本机（Windows）落盘就必须转码的后缀：cmd 按 ANSI 读 .bat/.cmd、PowerShell 5.1 无 BOM 时按 ANSI
# 读 .ps1（细则见 executor.encode_script_bytes）。批次 BM ②：此前转码只接在 SFTP 上传路径上，
# 本机上传与编辑器保存都是原样字节 → Windows 本机执行 .bat 的中文行直接乱码/炸。
WIN_LOCAL_ENCODED_SUFFIXES = (".bat", ".cmd", ".ps1")


def win_local_encode(data: bytes, name: str) -> tuple[bytes, Optional[str]]:
    """本机落盘前的平台转码（非 Windows 原样返回，Unix 行为不变）。返回 (payload, 警告|None)。"""
    if sys.platform != "win32" or Path(name).suffix.lower() not in WIN_LOCAL_ENCODED_SUFFIXES:
        return data, None
    return encode_script_bytes(data, True, name)


def atomic_write_bytes(path: Path, data: bytes):
    """脚本文件唯一的写盘路径：同目录临时文件 → fsync → os.replace（原子提交）。

    直接 `path.write_bytes(data)` 是整文件覆盖，写到一半崩溃/断电就把原文件截断，原内容没了
    （上传覆盖同名脚本、编辑保存都是这条路径）。同目录是硬要求：跨设备 os.replace 会抛
    OSError(EXDEV)。临时文件用 mkstemp（唯一名，同一目标并发写不互踩），异常路径 finally 清理。
    """
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())   # 不 fsync 可能出现「rename 已生效、内容还在页缓存」→ 崩了就空文件
        if path.exists():
            # 覆盖已有文件时保留原权限位：不然 0600 会把 0644/0755 覆盖掉（可执行位丢失）
            os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)   # 成功时已被 replace 移走（不存在）；失败路径清掉，不留 .tmp 垃圾
        except FileNotFoundError:
            pass

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


async def _last_run_map(db: AsyncSession, script_ids: list[int]) -> dict[int, "LastRunBrief"]:
    """SPEC §7.2-#1：批量取每个脚本的最近一次运行（单次聚合查询，N+1 禁止）。"""
    if not script_ids:
        return {}
    from ..models.run_history import RunHistory
    from ..schemas.brief import LastRunBrief
    subq = (
        select(RunHistory.script_id, func.max(RunHistory.id).label("mid"))
        .where(RunHistory.script_id.in_(script_ids))
        .group_by(RunHistory.script_id)
        .subquery()
    )
    rows = (await db.execute(
        select(RunHistory)
        .join(subq, RunHistory.id == subq.c.mid)
    )).scalars().all()
    return {
        r.script_id: LastRunBrief(  # type: ignore[dict-item]  # script_id 实际非空（按 script_id 分组）
            status=r.status, exit_code=r.exit_code,
            started_at=r.started_at.isoformat() if r.started_at else None,
            duration=r.duration,
        )
        for r in rows
    }


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
    items_out = _serialize_list(items, tag_map)
    # V5-G（SPEC §7.2-#1）：last_run 批量注入（单次聚合查询）
    last_run_map = await _last_run_map(db, [s.id for s in items])
    for it in items_out:
        lr = last_run_map.get(it["id"])
        if lr:
            it["last_run"] = lr.model_dump()
    return ScriptListOut(items=[ScriptOut(**it) for it in items_out],
                         total=total, page=page, page_size=page_size)


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
                    # 本机 Windows 与上传/保存走同一条落盘转码（批次 BM ②，见 win_local_encode）
                    data, enc_warning = win_local_encode(data, n)
                    if enc_warning:
                        logger.warning("导入 %s: %s", dest, enc_warning)
                    atomic_write_bytes(dest, data)
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
            # 替换主脚本的标签（同一唯一键上先删后插，必须语句级 DELETE + flush，
            # 否则重复导入已有标签的脚本会撞 uq_script_tag）
            await db.execute(delete(ScriptTag).where(ScriptTag.script_id == first_id))
            await db.flush()
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

    # 本机 Windows：.bat/.cmd 转 ANSI+CRLF、.ps1 加 BOM（批次 BM ②）——cmd/PowerShell 不按 UTF-8
    # 读脚本文件，原样落盘会让中文行乱码；转码失败（ANSI 编不下来）时原样落盘 + 警告透出，不静默。
    content, enc_warning = win_local_encode(content, safe_name)
    if enc_warning:
        logger.warning("上传 %s: %s", dest, enc_warning)

    atomic_write_bytes(dest, content)

    # 自动扫描
    result = await scan_scripts(db)
    return UploadResult(**result, message=f"已上传 {safe_name} 并扫描"
                         + (f"；{enc_warning}" if enc_warning else ""))

@router.put("/{script_id}/tags")
async def set_script_tags(script_id: int, data: TagsUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Script).where(Script.id == script_id))
    script = result.scalar_one_or_none()
    if not script:
        raise HTTPException(404, "Script not found")

    tag_ids = set(data.tag_ids)

    # 入参校验：不存在的 tag_id 走资源缺失语义（404 Tag not found），不得落成 500
    if tag_ids:
        found = (await db.execute(select(Tag.id).where(Tag.id.in_(tag_ids)))).scalars().all()
        missing = sorted(tag_ids - set(found))
        if missing:
            raise HTTPException(404, f"Tag not found: {missing}")

    # 删除旧的标签关联：必须走语句级 DELETE 并 flush——ORM 的 db.delete(成员)
    # 只是标记删除，flush 时 INSERT 先于 DELETE 发出，同一 (script_id, tag_id)
    # 上会撞 uq_script_tag → 再次保存含旧标签的集合必 500
    await db.execute(delete(ScriptTag).where(ScriptTag.script_id == script_id))
    await db.flush()

    # 添加新的
    for tag_id in tag_ids:
        db.add(ScriptTag(script_id=script_id, tag_id=tag_id))

    await db.commit()
    tag_map = await _load_tags(db, [script.id])
    return {"script_id": script_id, "tags": tag_map.get(script.id, [])}


# ── 脚本源码的文本编码：读取探测 + 按原编码写回（堵「静默乱码 → 写坏源文件」）────────────
# 实现搬到了 services/script_text.py（parser 也要按探测编码读源码，service 不能反过来 import
# router）；顶部导入，同时保持既有引用路径（含 tests 里的
# `from app.routers.script import probe_script_text`）不变。编码规则见该模块 docstring。


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

    raw = p.read_bytes()
    try:
        content, encoding = probe_script_text(raw)
    except ValueError as e:
        raise HTTPException(400, str(e))

    out = {"content": content, "language": script.category, "encoding": encoding}
    if encoding not in ("utf-8", "utf-8-sig"):
        out["encoding_warning"] = (
            f"该文件不是 UTF-8，当前按 {_encoding_label(encoding)} 解读；"
            f"保存时将按同一编码写回，不会改动文件编码。"
        )
    return out


@router.put("/{script_id}/content")
async def save_script_content(
    script_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """保存脚本内容：写入磁盘文件 + 触发重扫更新 DB 元数据

    编码规则（配合 get_script_content 的探测，堵「乱码写回=源文件永久损坏」）：
    - 请求未带 encoding → 按磁盘现状探测出的编码写回（默认路径：往返字节不变）
    - 请求带 encoding → 显式覆盖（确需转码时用；响应会说明编码已变更）
    - 内容含目标编码表达不了的字符 → 400 明确报错，绝不用 `?` 静默替换
    """
    result = await db.execute(select(Script).where(Script.id == script_id))
    script = result.scalar_one_or_none()
    if not script:
        raise HTTPException(404, "Script not found")

    content = payload.get("content", "")
    if not isinstance(content, str):
        raise HTTPException(400, "content 必须是字符串")

    p = Path(script.path)
    raw = p.read_bytes() if p.exists() else b""

    requested = payload.get("encoding")
    disk_encoding = None
    if requested:
        try:
            codecs.lookup(requested)   # 非法编码名 → 400（不落 500）
        except LookupError:
            raise HTTPException(400, f"未知编码: {requested}")
        encoding = requested
        if raw:
            try:
                disk_encoding = probe_script_text(raw)[1]
            except ValueError:
                disk_encoding = None
    else:
        try:
            # 原文件的编码即写回编码（服务端按磁盘现状判定，不依赖前端记忆）
            _, encoding = probe_script_text(raw) if raw else ("", "utf-8")
            disk_encoding = encoding
        except ValueError as e:
            # 原文件自身就解不出（二进制/未知编码）：默认拒绝写，需显式指定 encoding 才放行
            raise HTTPException(400, f"无法确定原文件编码（{e}）；确需覆盖请显式传 encoding")

    try:
        data = content.encode(encoding)
    except UnicodeEncodeError as e:
        raise HTTPException(400, (
            f"内容含 {_encoding_label(encoding)} 无法表示的字符"
            f"（{e.object[e.start:e.end]!r}，第 {e.start + 1} 个字符）——保存会丢字符，已拒绝。"
            f"请删掉该字符，或显式传 encoding=utf-8 把文件转存为 UTF-8。"
        ))

    # 行尾保全（批次 AJ）：编辑框（textarea）会把 CRLF 规范化成 LF，写盘前按**磁盘原文件**
    # 的行尾还原（磁盘上是 LF 就不动）。客户端不必记行尾状态，多端共用同一语义。
    data = preserve_eol(raw, data)

    # 本机 Windows 保存（批次 BM ②）：.bat/.cmd → ANSI+CRLF、.ps1 → UTF-8 BOM。
    # 此前只有 SFTP 上传（远端执行）在转码，本机保存/本机执行走的是原样字节 → cmd 按 ANSI 读
    # UTF-8 文件，中文行乱码。入参用 **UTF-8 源文本**（而非上面按磁盘编码编出的 data）：磁盘上已经是
    # ANSI 的文件二次保存时，若把 ANSI 字节喂给这个函数会被当成"非法 UTF-8"而误报警告。
    win_warning = None
    if sys.platform == "win32" and p.suffix.lower() in WIN_LOCAL_ENCODED_SUFFIXES:
        data, win_warning = encode_script_bytes(content.encode("utf-8"), True, p.name)
        # 落盘字节已不是上面的文本编码 → 以实际写入的编码回报，别让前端显示假的「当前编码」
        encoding = _win_ansi_codec() if p.suffix.lower() in (".bat", ".cmd") else "utf-8-sig"
        if win_warning:
            logger.warning("保存 %s: %s", p, win_warning)

    # 字节相同则跳过写盘（保持原优化）；写盘走原子提交，覆盖到一半崩溃不会截断原文件
    if not (p.exists() and raw == data):
        try:
            atomic_write_bytes(p, data)
        except OSError as e:
            raise HTTPException(500, f"写入文件失败: {e}")

    # V5-E：脚本被编辑 → 标记脏（下放任务的远端副本下次部署时增量同步）
    from ..services.sched_delegate import mark_dirty
    mark_dirty(script_id)

    # 重扫更新 DB 中的元数据（名称/路径/更新时间等）
    await scan_scripts(db)
    out = {"message": "已保存", "language": script.category, "encoding": encoding}
    notes = []
    if disk_encoding and disk_encoding != encoding:
        notes.append(
            f"已按 {_encoding_label(encoding)} 重写，原文件编码为 {_encoding_label(disk_encoding)}（编码已变更）"
        )
    if win_warning:
        notes.append(win_warning)
    if notes:
        out["encoding_warning"] = " ".join(notes)
    return out


@router.get("/{script_id}/env-check")
async def env_check(script_id: int, device_id: int | None = None,
                    db: AsyncSession = Depends(get_db)):
    """手动触发某脚本的环境检测（平台兼容 + 运行时版本）。

    `device_id`（query，可选）= **判定对象**：
      · 不传 / null → **Server 本机**（与 run.py 的本机执行前置同一判据，行为与旧版一致）；
      · 传了 → **目标设备**：用它的 probe 缓存（ssh_service.fresh_entry：切设备自动重探 /
        「测试连接」/ 本机信息页探测写入的那一份）构造远端判据。此前不管当前设备是谁都探
        Server 本机 → 在 Windows 设备的脚本工作区点「环境检测」永远报 unix（批次 AO 修的缺口）。

    ⚠️ **本端点不现探**：一次 remote_probe 最长 5 条命令 × 10s（不可达设备 60s 级），一个按钮
    等不起，而且会把整页卡住。缓存缺失/过期 → 明确回「无探测结论」（`conclusive: false` + note），
    **不判红、也不拿 Server 本机冒充目标机**（那正是本批次要修的错判）。刷新结论的入口已经存在：
    设备页「测试连接」/ 本机信息页「↻ 重新探测」/ 切设备时的自动重探。
    """
    result = await db.execute(select(Script).where(Script.id == script_id))
    script = result.scalar_one_or_none()
    if not script:
        raise HTTPException(404, "Script not found")

    if device_id is None:
        checks = check_environment(script)
        return {
            "script_id": script_id,
            "checks": checks,
            "unmet": [c for c in checks if not c["ok"]],
            "all_ok": all(c["ok"] for c in checks),
            "source": "local", "device_id": None, "conclusive": True, "note": None,
        }

    dev = (await db.execute(select(Device).where(Device.id == device_id))).scalar_one_or_none()
    if not dev:
        raise HTTPException(404, "Device not found")

    ent = fresh_entry(device_id)
    if not ent or not ent.get("platform"):
        # 无新鲜结论（从未探测 / 已过 TTL 600s / 上次探测连不上）→ 「无数据」而不是「不通过」：
        # checks=[] 表示**一项都没判**，conclusive=False 让调用方能把它和「有结论且全过」分开。
        # all_ok=False 取「无结论即不算通过」的保守含义（别让调用方把空判定当绿灯）。
        why = (f"「{dev.name}」最近一次探测失败：{ent.get('error')}" if (ent and not ent.get("ok"))
               else f"「{dev.name}」没有未过期的环境探测结论")
        return {
            "script_id": script_id,
            "checks": [],
            "unmet": [],
            "all_ok": False,
            "source": "device", "device_id": device_id, "conclusive": False,
            "note": why + " —— 请到「远程设备」页点「测试连接」，或在本机信息页点「↻ 重新探测」后重试",
        }

    checks = check_environment(script, device_platform=ent.get("platform"),
                               device_runtimes=ent.get("runtimes") or [])
    return {
        "script_id": script_id,
        "checks": checks,
        "unmet": [c for c in checks if not c["ok"]],
        "all_ok": all(c["ok"] for c in checks),
        "source": "device", "device_id": device_id, "conclusive": True, "note": None,
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

    try:
        params = parse_script(p, script.category)
    except ValueError as e:
        # 编码探测失败（二进制/未知编码）→ 400 明确报错，不落 500（解析器不再静默返回空参数表）
        raise HTTPException(400, str(e))
    script.parameters = json.dumps(params, ensure_ascii=False)
    await db.commit()
    await db.refresh(script)

    return {"id": script.id, "parameters": params}