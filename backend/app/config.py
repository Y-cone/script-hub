import json
import os
from pathlib import Path


def _settings_path() -> Path:
    """设置文件落 OS 配置目录（SPEC §2.9：不得存于数据目录内）。"""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / "com.scripthub.app" / "settings.json"


CONFIG_PATH = _settings_path()

_REPO_DATA_DIR = Path(__file__).parent.parent.parent / "data"


def _saved_data_dir() -> Path | None:
    """settings.json 里已保存的 data_dir（坏文件/缺字段 → None）。"""
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
        v = cfg.get("data_dir") if isinstance(cfg, dict) else None
        return Path(v) if v else None
    except Exception:
        return None


# 数据目录优先级（SPEC §7.2 v2.14）：
# SCRIPTHUB_DATA_DIR（外部覆盖）> settings.json 的 data_dir > SCRIPTHUB_DEFAULT_DATA_DIR（壳注入默认）> 仓库 data/
_env_data_dir = os.environ.get("SCRIPTHUB_DATA_DIR")
_saved = None if _env_data_dir else _saved_data_dir()
_default_env = os.environ.get("SCRIPTHUB_DEFAULT_DATA_DIR")
DATA_DIR = (
    Path(_env_data_dir) if _env_data_dir
    else _saved if _saved is not None
    else Path(_default_env) if _default_env
    else _REPO_DATA_DIR
)

# 旧配置位置（兼容读，v2.11 前落在数据目录内——切数据目录会把配置本身丢掉，已废弃）
_LEGACY_CONFIG_PATH = DATA_DIR / "config.json"


DEFAULT_CONFIG = {"script_root_dir": str(DATA_DIR / "scripts")}


def load_config() -> dict:
    """读运行配置。优先级 env > settings.json > 旧 config.json > 默认。

    兼容：新 settings.json 不存在时回落读旧 DATA_DIR/config.json（老用户配置不丢）；
    只有两者都无（新装）才在旧位置播种默认值（保持 get_script_root 等既有调用方行为不变）。
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg: dict = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except Exception:
            cfg = {}  # 坏文件回落默认，不阻塞启动
    elif _LEGACY_CONFIG_PATH.exists():
        try:
            cfg = json.loads(_LEGACY_CONFIG_PATH.read_text())
        except Exception:
            cfg = {}
    else:
        _LEGACY_CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
        cfg = dict(DEFAULT_CONFIG)
    if not isinstance(cfg, dict):
        cfg = {}
    # env 覆盖（运行期生效；SAVE 不得写入 env 决定的值）
    if os.environ.get("SCRIPTHUB_SCRIPTS_ROOT"):
        cfg["script_root_dir"] = os.environ["SCRIPTHUB_SCRIPTS_ROOT"]
    return cfg


def save_config(config: dict):
    """保存到 OS 配置目录的 settings.json（不再写数据目录）。"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


def get_script_root() -> Path:
    cfg = load_config()
    return Path(cfg["script_root_dir"])
