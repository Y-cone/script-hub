from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event, text

from .config import DATA_DIR

DB_PATH = DATA_DIR / "scripthub.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}", echo=False, connect_args={"timeout": 30})


# SQLite 默认关闭外键约束，需在每次连接后显式开启
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
    ("scripts", "env_requests", "TEXT"),
    ("scripts", "dependencies", "TEXT"),
    ("run_history", "is_scheduled", "INTEGER DEFAULT 0"),
    ("run_history", "schedule_id", "INTEGER"),
    ("run_history", "device_id", "INTEGER"),
    ("schedules", "device_id", "INTEGER"),
    ("schedules", "exec_location", "VARCHAR(10) DEFAULT 'local'"),
]

# 需要重建表以更新外键/可空约束的表：{表名: 建表DDL}
# 背景：run_history.script_id 需改为可空 + ON DELETE SET NULL，以支持删除脚本时保留历史
_TABLE_REBUILDS = {
    "run_history": """
        CREATE TABLE run_history_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            script_id INTEGER REFERENCES scripts(id) ON DELETE SET NULL,
            parameters TEXT NOT NULL DEFAULT '{}',
            command VARCHAR(2048) NOT NULL DEFAULT '',
            output TEXT NOT NULL DEFAULT '',
            output_file VARCHAR(1024),
            exit_code INTEGER,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            duration FLOAT,
            started_at DATETIME,
            finished_at DATETIME
        )
    """,
}


async def _apply_migrations():
    async with engine.begin() as conn:
        for table, column, ddl in _COLUMN_MIGRATIONS:
            q = text(
                f"SELECT COUNT(*) FROM pragma_table_info('{table}') WHERE name = '{column}'"
            )
            res = await conn.execute(q)
            if res.scalar() == 0:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


async def _rebuild_tables():
    """重建部分表以更新约束（幂等：仅当表未带目标约束时重建）"""
    async with engine.begin() as conn:
        for table, ddl_template in _TABLE_REBUILDS.items():
            # 检查旧表外键是否含 ON DELETE SET NULL（已迁移则跳过）
            fk_row = (
                await conn.execute(
                    text(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table}'")
                )
            ).scalar()
            if fk_row and "ON DELETE SET NULL" in fk_row:
                continue  # 已迁移

            cols = (
                await conn.execute(
                    text(f"SELECT name FROM pragma_table_info('{table}')")
                )
            ).all()
            col_names = [c[0] for c in cols]
            col_list = ", ".join(f'"{c}"' for c in col_names)

            # 建新表
            await conn.execute(text(ddl_template))

            # 复制数据
            await conn.execute(text(f'INSERT INTO run_history_new ({col_list}) SELECT {col_list} FROM {table}'))

            # 删旧表，改名
            await conn.execute(text(f"DROP TABLE {table}"))
            await conn.execute(text(f"ALTER TABLE run_history_new RENAME TO {table}"))


async def init_db():
    # 先建表（幂等）再补列/重建——空库（全新数据目录）时 _apply_migrations 的
    # ALTER 会因表不存在而失败，create_all 必须在前
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _apply_migrations()
    await _rebuild_tables()


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session