import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / "data"
CONFIG_PATH = DATA_DIR / "config.json"

DEFAULT_CONFIG = {"script_root_dir": str(DATA_DIR / "scripts")}


def load_config() -> dict:
    DATA_DIR.mkdir(exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
    return json.loads(CONFIG_PATH.read_text())


def save_config(config: dict):
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


def get_script_root() -> Path:
    cfg = load_config()
    return Path(cfg["script_root_dir"])
