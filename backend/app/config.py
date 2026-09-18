import json
import os
from pathlib import Path

# 数据目录解析顺序（PRD-V5 4.G）：
# 1. SCRIPTHUB_DATA_DIR 环境变量（Tauri sidecar 注入 / 测试覆盖）
# 2. 默认：仓库 data/（Web dev 形态，与历史行为一致）
_DATA_DIR_ENV = os.environ.get("SCRIPTHUB_DATA_DIR")
DATA_DIR = Path(_DATA_DIR_ENV) if _DATA_DIR_ENV else Path(__file__).parent.parent.parent / "data"
CONFIG_PATH = DATA_DIR / "config.json"

DEFAULT_CONFIG = {"script_root_dir": str(DATA_DIR / "scripts")}


def load_config() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
    return json.loads(CONFIG_PATH.read_text())


def save_config(config: dict):
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


def get_script_root() -> Path:
    cfg = load_config()
    return Path(cfg["script_root_dir"])
