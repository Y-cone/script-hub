from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event, text
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "data" / "scripthub.db"
DB_PATH.parent.mkdir(exist_ok=True)

engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}", echo=False)


# SQLite 默认关闭外键约束，需在每次连接后显式开启（否则 ON DELETE CASCADE 不生效）
@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# 轻量列迁移：为已存在的表补充新列（幂等）
_COLUMN_MIGRATIONS = [
    ("scripts", "source", "VARCHAR(20) DEFAULT 'scan'"),
]


async def _apply_migrations():
    async with engine.begin() as conn:
        for table, column, ddl in _COLUMN_MIGRATIONS:
            q = text(
                f"SELECT COUNT(*) FROM pragma_table_info('{table}') WHERE name = '{column}'"
            )
            res = await conn.execute(q)
            if res.scalar() == 0:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


async def init_db():
    await _apply_migrations()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session