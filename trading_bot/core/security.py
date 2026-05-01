import json
import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from config.settings import get_settings


def _get_fernet() -> Fernet:
    settings = get_settings()
    if not settings.SECRET_KEY:
        raise ValueError("SECRET_KEY not configured in .env")
    return Fernet(settings.SECRET_KEY.encode())


def encrypt(plain_text: str) -> str:
    if not plain_text:
        return ""
    f = _get_fernet()
    return f.encrypt(plain_text.encode()).decode()


def decrypt(cipher_text: str) -> str:
    if not cipher_text:
        return ""
    try:
        f = _get_fernet()
        return f.decrypt(cipher_text.encode()).decode()
    except InvalidToken:
        return ""


def load_keys() -> dict:
    keys_path = Path("data/keys.enc")
    if not keys_path.exists():
        return {}
    try:
        with open(keys_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}


def save_keys(keys: dict) -> None:
    os.makedirs("data", exist_ok=True)
    with open("data/keys.enc", "w") as f:
        json.dump(keys, f, indent=2)


def get_credential(name: str) -> Optional[str]:
    keys = load_keys()
    encrypted = keys.get(name)
    if encrypted:
        return decrypt(encrypted)
    return None


def set_credential(name: str, value: str) -> None:
    keys = load_keys()
    if value:
        keys[name] = encrypt(value)
    else:
        keys.pop(name, None)
    save_keys(keys)


def delete_credential(name: str) -> None:
    keys = load_keys()
    keys.pop(name, None)
    save_keys(keys)


def has_credential(name: str) -> bool:
    keys = load_keys()
    return name in keys