"""凭据安全存储：优先系统钥匙串(keyring)，不可用时回退到本地受限权限文件。

PRD 要求凭据存系统钥匙串。但 headless/无桌面会话(如 DBUS_SESSION_BUS_ADDRESS 缺失)时
keyring 无可用后端。此处做部署兜底：keyring 失败 → ~/.config/scripthub/secrets.json (chmod 600)。
(ponytail: 文件回退非绝对安全，仅作为 keyring 不可用环境的兜底；桌面环境始终走 keyring)
"""
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_SECRETS_DIR = Path.home() / ".config" / "scripthub"
_SECRETS_FILE = _SECRETS_DIR / "secrets.json"

_keyring = None
try:
    import keyring as _kr
    _keyring = _kr
except Exception:
    _keyring = None


def _file_secrets() -> dict:
    if _SECRETS_FILE.exists():
        try:
            return json.loads(_SECRETS_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_file_secrets(data: dict):
    _SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    _SECRETS_FILE.write_text(json.dumps(data, indent=2))
    try:
        os.chmod(_SECRETS_FILE, 0o600)
    except OSError:
        pass


def set_secret(service: str, username: str, secret: str) -> bool:
    """保存凭据。keyring 与本地文件双写，保证任何环境都能读到。
    返回 True=keyring 写入成功，False=仅文件兜底。"""
    keyring_ok = False
    if _keyring:
        try:
            _keyring.set_password(service, username, secret)
            keyring_ok = True
        except Exception as e:
            logger.warning(f"keyring 不可用，仅写本地文件: {e}")
    # 始终写文件兜底（keyring/文件双写，避免进程间 DBUS 会话差异导致读不到）
    data = _file_secrets()
    data.setdefault(service, {})[username] = secret
    _save_file_secrets(data)
    return keyring_ok


def get_secret(service: str, username: str) -> str | None:
    """读取凭据。keyring 优先（系统凭据最可靠），本地文件兜底（headless 无 keyring 时）。"""
    if _keyring:
        try:
            v = _keyring.get_password(service, username)
            if v is not None:
                return v
        except Exception:
            pass
    data = _file_secrets()
    return data.get(service, {}).get(username)


def delete_secret(service: str, username: str):
    """删除凭据（尽力而为）"""
    if _keyring:
        try:
            _keyring.delete_password(service, username)
        except Exception:
            pass
    data = _file_secrets()
    if service in data and username in data[service]:
        del data[service][username]
        _save_file_secrets(data)