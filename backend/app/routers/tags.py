from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from ..database import get_db
from ..models.tag import Tag
from ..schemas.tag import TagOut, TagCreate, TagUpdate, TagListOut
from sqlalchemy.exc import IntegrityError

router = APIRouter(prefix="/api/tags", tags=["tags"])


@router.get("", response_model=TagListOut)
async def list_tags(
    search: str = Query("", description="按名称搜索"),
    db: AsyncSession = Depends(get_db),
):
    query = select(Tag)
    count_query = select(func.count(Tag.id))
    if search:
        like = f"%{search}%"
        query = query.where(Tag.name.ilike(like))
        count_query = count_query.where(Tag.name.ilike(like))

    result = await db.execute(count_query)
    total = result.scalar() or 0

    query = query.order_by(Tag.name)
    result = await db.execute(query)
    items = list(result.scalars().all())

    return TagListOut(items=items, total=total)


@router.post("", response_model=TagOut)
async def create_tag(data: TagCreate, db: AsyncSession = Depends(get_db)):
    tag = Tag(name=data.name.strip(), color=data.color)
    db.add(tag)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # SPEC §2.3/§6.7 v2.16：重名由后端 409 拒绝（原 400 与定稿规范不符；前端在输入行下红字提示）
        raise HTTPException(409, f"标签已存在: {data.name}")
    await db.refresh(tag)
    return tag


@router.put("/{tag_id}", response_model=TagOut)
async def update_tag(tag_id: int, data: TagUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Tag).where(Tag.id == tag_id))
    tag = result.scalar_one_or_none()
    if not tag:
        raise HTTPException(404, "Tag not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(tag, field, value)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # SPEC §2.3/§6.7 v2.16：重名（改名撞已有名）同 409
        raise HTTPException(409, f"标签名已存在: {data.name}")
    await db.refresh(tag)
    return tag


@router.delete("/{tag_id}")
async def delete_tag(tag_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Tag).where(Tag.id == tag_id))
    tag = result.scalar_one_or_none()
    if not tag:
        raise HTTPException(404, "Tag not found")

    # 删除标签（script_tags 通过级联清理）
    await db.delete(tag)
    await db.commit()
    return {"message": "已删除"}