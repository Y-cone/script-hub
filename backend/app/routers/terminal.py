"""终端 WebSocket（PRD V4 / V5-C）：/api/terminal/ws — 会话建连 + 双向流。

建连：URL 查询参数 ?device_id=&shell=&cols=&rows=，连接后首帧 ready(session_id)。
此后 WS 内 JSON 消息：input/resize/close ↔ output/exit/error。
"""
import asyncio
import sys
import uuid
import logging
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from ..database import async_session
from ..models.device import Device
from ..services.session_manager import registry, resolve_terminal_shell
from ..services.ssh_service import is_win_device

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/terminal", tags=["terminal"])


@router.websocket("/ws")
async def terminal_ws(websocket: WebSocket,
                      device_id: int = 0,
                      shell: str = "",
                      cols: int = 80,
                      rows: int = 24):
    """终端会话。device_id=0 → 本机会话；>0 → 远程设备会话。"""
    await websocket.accept()

    session_id = uuid.uuid4().hex[:12]
    out_queue: asyncio.Queue = asyncio.Queue()

    def push_output(text: str):
        # 读线程（pty/paramiko recv）调用；asyncio.Queue 线程安全
        try:
            out_queue.put_nowait(text)
        except Exception:
            pass

    # 建会话（shell 类型先过白名单：非法值 → error 帧 + 关闭，不静默退回默认）
    try:
        if device_id and device_id > 0:
            async with async_session() as db:
                dev = (await db.execute(select(Device).where(Device.id == device_id))).scalar_one_or_none()
            if not dev:
                await websocket.send_json({"type": "error", "message": "设备不存在"})
                await websocket.close()
                return
            # 平台判据唯一入口：实时探测（与手动执行/定时下放同一判据），不读 device.type
            is_win = await is_win_device(dev)
            shell = resolve_terminal_shell(shell, is_win)
            await registry.create_remote(
                session_id, push_output, dev,
                cols=max(cols, 1), rows=max(rows, 1), shell=shell or "",
                is_windows=is_win)
        else:
            shell = resolve_terminal_shell(shell, sys.platform.startswith("win"))
            await registry.create_local(
                session_id, push_output, shell=shell or None,
                cols=max(cols, 1), rows=max(rows, 1))
    except Exception as e:
        logger.error(f"建终端会话失败: {e}")
        await websocket.send_json({"type": "error", "message": f"会话建立失败: {str(e)}"})
        await websocket.close()
        return

    await websocket.send_json({"type": "ready", "session_id": session_id})

    # 读线程产出 → 本协程消费 output
    async def _output_pump():
        while True:
            text = await out_queue.get()
            await websocket.send_json({"type": "output", "data": text})

    # 建会话后启动 output 泵（与接收循环并行）
    output_task = asyncio.create_task(_output_pump())

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
                await registry.remove(session_id)
                break
    except WebSocketDisconnect:
        pass
    finally:
        output_task.cancel()
        await registry.remove(session_id)
