from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from .database import init_db, async_session
from .routers.script import router as script_router
from .routers.tags import router as tags_router
from .routers.system import router as system_router
from .routers.run import router as run_router
from .routers.schedules import router as schedules_router
from .routers.device import router as device_router
from .services.scheduler_service import scheduler_service
from .services.scanner import scan_scripts
import asyncio
import logging

logger = logging.getLogger(__name__)

# 自动同步：每 30 秒轮询扫描脚本目录（PRD 2.4）
AUTO_SYNC_INTERVAL = 30


async def _auto_sync_loop():
    """后台自动同步：定期调用 scan_scripts，mtime 判断变更，异常不中断"""
    while True:
        await asyncio.sleep(AUTO_SYNC_INTERVAL)
        try:
            async with async_session() as db:
                await scan_scripts(db)
        except Exception as e:
            logger.error(f"自动同步失败: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await scheduler_service.start()
    sync_task = asyncio.create_task(_auto_sync_loop())
    yield
    sync_task.cancel()
    scheduler_service.shutdown()


app = FastAPI(title="ScriptHub", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理"""
    logger.error(f"Unhandled error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请查看日志"}
    )


app.include_router(script_router)
app.include_router(tags_router)
app.include_router(system_router)
app.include_router(run_router)
app.include_router(schedules_router)
app.include_router(device_router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
