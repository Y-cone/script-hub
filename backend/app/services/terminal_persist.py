"""终端会话持久化（PRD-V5 4.F / Phase V5-C）。

定义（4.F 边界）：持久化 = 会话元数据 + 回滚缓冲（scrollback 尾部）。
进程本身不可跨重启保留——恢复为「未连接态标签 + 只读历史 + 一键重连」。

数据流：
- SessionHandle 挂 RingBuffer（尾 500 行），terminal.py 的 push_output 同步追加
- 写盘时机：会话创建/关闭/每 30s 节流（ScrollbackFlusher 后台任务 + stop 终落盘）
- 存储：DATA_DIR/terminal_sessions.json
"""
import asyncio
import json
import logging
import threading
from datetime import datetime
from pathlib import Path

from ..config import DATA_DIR

logger = logging.getLogger(__name__)

SESSIONS_FILE = DATA_DIR / "terminal_sessions.json"
SCROLLBACK_LINES = 500
_FLUSH_INTERVAL = 30  # 秒


class ScrollbackBuffer:
    """线程安全环形缓冲（尾 N 行）。push_output 从读线程调用。"""

    def __init__(self, max_lines: int = SCROLLBACK_LINES):
        self._lines: list[str] = []
        self._max = max_lines
        self._lock = threading.Lock()
        self._partial = ""  # 未换行的残段

    def append(self, text: str):
        with self._lock:
            self._partial += text
            *done, self._partial = self._partial.split("\n")
            self._lines.extend(done)
            if len(self._lines) > self._max:
                self._lines = self._lines[-self._max:]

    def tail(self) -> str:
        with self._lock:
            out = "\n".join(self._lines)
            if self._partial:
                out += "\n" + self._partial
            return out


class SessionPersistence:
    """terminal_sessions.json 读写（线程安全，内存态 + 节流落盘）。"""

    def __init__(self, path: Path = SESSIONS_FILE):
        self.path = path
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}
        self._load()

    def _load(self):
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text())
                for s in data.get("sessions", []):
                    self._sessions[s["tab_id"]] = s
                logger.info(f"终端会话持久化: 恢复 {len(self._sessions)} 条记录")
        except Exception as e:
            logger.warning(f"terminal_sessions.json 读取失败（按空处理）: {e}")

    def upsert(self, tab_id: str, device_id: int | None, shell: str, title: str,
               scrollback: str):
        with self._lock:
            self._sessions[tab_id] = {
                "tab_id": tab_id,
                "device_id": device_id,
                "shell": shell,
                "title": title,
                "created_at": self._sessions.get(tab_id, {}).get(
                    "created_at", datetime.now().isoformat()),
                "scrollback": scrollback[-SCROLLBACK_LINES * 200:],  # 字符截尾防膨胀
            }

    def remove(self, tab_id: str):
        with self._lock:
            self._sessions.pop(tab_id, None)

    def list_sessions(self) -> list[dict]:
        with self._lock:
            return list(self._sessions.values())

    def flush(self):
        """落盘（原子写）。"""
        with self._lock:
            data = {"sessions": list(self._sessions.values())}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False))
            tmp.replace(self.path)
        except Exception as e:
            logger.warning(f"terminal_sessions.json 写盘失败: {e}")


# 全局单例（terminal.py / session_manager 共用）
persistence = SessionPersistence()


class ScrollbackFlusher:
    """30s 节流落盘后台任务（FastAPI lifespan 启动/停止）。"""

    def __init__(self, store: SessionPersistence = persistence):
        self.store = store
        self._task = None

    async def start(self):
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None
        self.store.flush()  # 停止前最终落盘

    async def _loop(self):
        while True:
            await asyncio.sleep(_FLUSH_INTERVAL)
            try:
                self.store.flush()
            except Exception:
                pass
