"""终端 WebSocket（PRD V4 / V5-C）：/api/terminal/ws — 会话建连 + 双向流 + 持久化。

建连：URL 查询参数 ?device_id=&shell=&cols=&rows=&tab_id=，连接后首帧 ready(session_id)。
此后 WS 内 JSON 消息：input/resize/close ↔ output/exit/error。

V5-C 持久化（4.F）：
- 每会话 scrollback 由 SessionHandle.scrollback 缓冲（后端记录，前端不回传）
- WS 断开 ≠ 删持久化（标签恢复用）；只有前端发 close（用户关标签）才移除
- 恢复：GET /api/terminal/sessions 返回持久化标签列表（含 scrollback）
"""
import asyncio
import uuid
import logging
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from ..database import async_session
from ..models.device import Device
from ..services.session_manager import registry
from ..services.terminal_persist import persistence

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/terminal", tags=["terminal"])


@router.get("/sessions")
async def list_persisted_sessions():
    """持久化的终端标签（应用重启后恢复用）：元数据 + scrollback。"""
    return {"sessions": persistence.list_sessions()}


@router.delete("/sessions/{tab_id}")
async def drop_persisted_session(tab_id: str):
    """移除持久化标签（前端「关闭已结束的恢复标签」时调用）。"""
    persistence.remove(tab_id)
    persistence.flush()
    return {"ok": True}


@router.websocket("/ws")
async def terminal_ws(websocket: WebSocket,
                      device_id: int = 0,
                      shell: str = "",
                      cols: int = 80,
                      rows: int = 24,
                      tab_id: str = ""):
    """终端会话。device_id=0 → 本机会话；>0 → 远程设备会话。tab_id → 持久化关联键。"""
    await websocket.accept()

    session_id = uuid.uuid4().hex[:12]
    tab_id = tab_id or session_id  # 前端可传固定 tab_id 以便重启恢复
    out_queue: asyncio.Queue = asyncio.Queue()

    def push_output(text: str):
        # 读线程（pty/paramiko recv）调用；asyncio.Queue 线程安全
        # V5-C：SessionHandle._emit 已同步进 scrollback，这里只管 WS 推送
        try:
            out_queue.put_nowait(text)
        except Exception:
            pass

    # 建会话
    try:
        if device_id and device_id > 0:
            async with async_session() as db:
                dev = (await db.execute(select(Device).where(Device.id == device_id))).scalar_one_or_none()
            if not dev:
                await websocket.send_json({"type": "error", "message": "设备不存在"})
                await websocket.close()
                return
            await registry.create_remote(
                session_id, push_output, dev,
                cols=max(cols, 1), rows=max(rows, 1), shell=shell or "")
        else:
            await registry.create_local(
                session_id, push_output, shell=shell or None,
                cols=max(cols, 1), rows=max(rows, 1))
    except Exception as e:
        logger.error(f"建终端会话失败: {e}")
        await websocket.send_json({"type": "error", "message": f"会话建立失败: {str(e)}"})
        await websocket.close()
        return

    sess = registry.get(session_id)

    # V5-C：持久化 upsert（建会话时记录元数据）
    persistence.upsert(tab_id, device_id or None, shell or "", "", "")
    persistence.flush()

    await websocket.send_json({"type": "ready", "session_id": session_id})

    # 读线程产出 → 本协程消费 output
    async def _output_pump():
        while True:
            text = await out_queue.get()
            await websocket.send_json({"type": "output", "data": text})

    # 建会话后启动 output 泵（与接收循环并行）
    output_task = asyncio.create_task(_output_pump())

    user_closed = False
    try:
        # 接收前端消息
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type")
            sess = registry.get(session_id)
            if sess is None:
                continue  # 会话已关闭
            if mtype == "input":
                sess.send(msg.get("data", ""))
            elif mtype == "resize":
                sess.resize(int(msg.get("cols", 80)), int(msg.get("rows", 24)))
            elif mtype == "close":
                user_closed = True
                await registry.remove(session_id)
                break
    except WebSocketDisconnect:
        pass
    finally:
        output_task.cancel()
        await registry.remove(session_id)
        if user_closed:
            # 用户主动关标签 → 移除持久化记录（回滚缓冲不需要了）
            persistence.remove(tab_id)
        else:
            # 断线/壳退出 → 保留 scrollback（恢复标签用）
            if sess:
                persistence.upsert(tab_id, device_id or None, shell or "",
                                   "", sess.scrollback.tail())
        persistence.flush()
