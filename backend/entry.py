"""ScriptHub sidecar 入口（PRD-V5 / Phase V5-A）。

由 Tauri 壳以子进程方式拉起，或开发期直接 `python entry.py` 运行：
- SCRIPTHUB_PORT     监听端口（默认 8001）
- SCRIPTHUB_DATA_DIR 数据目录（默认仓库 data/；桌面形态指向用户数据目录）
- 仅监听 127.0.0.1（桌面形态 API 只服务本机 WebView）
"""
import os
import sys
import threading
import time
from pathlib import Path

_BACKEND_DIR = Path(__file__).parent
sys.path.insert(0, str(_BACKEND_DIR))


def _watch_parent():
    """父进程看护（PRD-V5 风险表：壳崩溃 → sidecar 僵尸）：
    壳进程消失（getppid 变 1）→ 自杀。sidecar 由壳以子进程拉起，
    正常退出时壳会 kill_tree；此线程只兜底壳被强杀的场景。
    """
    ppid = os.getppid()
    while True:
        time.sleep(2)
        if os.getppid() != ppid:
            os._exit(0)  # 父亡，立即退出（不等优雅关闭）


def main():
    # 数据目录必须在 app 模块 import 前定案（config/database/executor 均由此推导）。
    # SCRIPTHUB_DATA_DIR = 显式覆盖（测试/开发）；SCRIPTHUB_DEFAULT_DATA_DIR = Tauri 壳
    # 注入的默认（main.rs）——之前只读前者导致迁移分支在桌面形态恒假（门禁 9 FAIL 根因）。
    data_dir = (os.environ.get("SCRIPTHUB_DATA_DIR")
                or os.environ.get("SCRIPTHUB_DEFAULT_DATA_DIR"))
    if data_dir:
        # 一次性迁移：旧数据目录 -> 用户数据目录（幂等，见 datamigrate.migrate_if_needed）
        # 旧目录来源：SCRIPTHUB_OLD_DATA_DIR（Tauri 壳注入；源码运行默认推导仓库 data/）。
        # 注意 onefile 下 __file__ 指向解压临时目录，不能作为仓库锚点——故需显式注入。
        from app.datamigrate import migrate_if_needed
        old_dir = os.environ.get("SCRIPTHUB_OLD_DATA_DIR") or str(_BACKEND_DIR.parent / "data")
        try:
            migrate_if_needed(Path(old_dir), Path(data_dir))
        except Exception as e:
            print(f"[scripthub-server] 数据迁移失败（跳过，用现有数据启动）: {e}", flush=True)

    import uvicorn
    from app.config import DATA_DIR

    port = int(os.environ.get("SCRIPTHUB_PORT", "8001"))
    print(f"[scripthub-server] port={port} data_dir={DATA_DIR}", flush=True)

    # 父进程看护线程（守护线程，uvicorn 主循环退出时自动结束）
    threading.Thread(target=_watch_parent, daemon=True).start()

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
