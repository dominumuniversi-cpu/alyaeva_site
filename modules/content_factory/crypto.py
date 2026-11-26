"""Шифрование секретов модуля — через шифровальщик сайта.

Токены площадок и ключи нейросетей в базе лежат шифром, как токены мессенджеров.
Каким именно — решает сайт: у сайта уже есть secrets_helper (SECRET_KEY → Fernet).
Модуль просит две функции — encrypt(str) и decrypt(str) — и не знает ни ключа, ни алгоритма.
"""
from __future__ import annotations

_enc = None
_dec = None


def connect(encrypt, decrypt) -> None:
    """Подключить шифровальщик сайта."""
    global _enc, _dec
    if not callable(encrypt) or not callable(decrypt):
        raise TypeError("connect(encrypt, decrypt): нужны две функции")
    _enc, _dec = encrypt, decrypt


def encrypt(value: str) -> str:
    """Зашифровать. Пустая строка остаётся пустой: токена нет — так и записываем."""
    if not value:
        return ""
    if _enc is None:
        raise RuntimeError("content_factory: шифрование не подключено (cf.secure(encrypt, decrypt))")
    return _enc(value)


def decrypt(value: str) -> str:
    """Расшифровать. Старое значение без шифра вернётся как есть — так работал secrets_helper."""
    if not value:
        return ""
    if _dec is None:
        raise RuntimeError("content_factory: шифрование не подключено (cf.secure(encrypt, decrypt))")
    return _dec(value)
