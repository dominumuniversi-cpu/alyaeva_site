"""Шифрование секретов (токены ботов, пароли интеграций) в БД.

Токены Telegram/VK не лежат открытым текстом: в app_settings пишется
"enc:<base64>". Ключ шифрования — производный от SECRET_KEY.
Формат: Fernet (AES-128-CBC + HMAC), стандарт для подобных задач.
"""
from __future__ import annotations

import base64
import hashlib

from config import config

_PREFIX = "enc:"


def _fernet():
    """Получить объект Fernet из SECRET_KEY."""
    from cryptography.fernet import Fernet
    key = base64.urlsafe_b64encode(hashlib.sha256(config.SECRET_KEY.encode()).digest())
    return Fernet(key)


def encrypt(value: str) -> str:
    """Зашифровать значение. Пустая строка остаётся пустой."""
    if not value:
        return ""
    return _PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value: str) -> str:
    """Расшифровать значение. Не расшифровалось — вернуть как есть (старые данные)."""
    if not value:
        return ""
    if not str(value).startswith(_PREFIX):
        return str(value)
    try:
        return _fernet().decrypt(str(value)[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except Exception:
        return ""


def mask(value: str) -> str:
    """Маска для показа в интерфейсе: видны только последние 4 символа."""
    if not value or len(value) < 8:
        return "—"
    return "***" + value[-4:]
