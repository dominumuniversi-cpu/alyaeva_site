"""Доступ к PostgreSQL: пул соединений, настройки, пользователи, сессии."""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from config import config

# Пул соединений на всё приложение
pool = ConnectionPool(config.dsn, min_size=1, max_size=10, open=False, kwargs={"row_factory": dict_row})


def open_pool() -> None:
    """Открыть пул при старте приложения."""
    pool.open()


def close_pool() -> None:
    """Закрыть пул при остановке."""
    pool.close()


# ---------------- Настройки ----------------

def get_setting(key: str, default: Any = None) -> Any:
    """Прочитать настройку по ключу-пути (значение хранится JSON-строкой)."""
    with pool.connection() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = %s", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except (ValueError, TypeError):
        return row["value"]


def get_secret(key: str, default: str = "") -> str:
    """Прочитать секрет (токен). Расшифровка — в secrets_helper."""
    import secrets_helper
    return secrets_helper.decrypt(get_setting(key, default))


def set_setting(key: str, value: Any, user_id: int | None = None) -> None:
    """Записать настройку и записать изменение в журнал аудита."""
    raw = json.dumps(value, ensure_ascii=False)
    with pool.connection() as conn:
        old = conn.execute("SELECT value FROM app_settings WHERE key = %s", (key,)).fetchone()
        conn.execute(
            """INSERT INTO app_settings (key, value, updated_at, updated_by)
               VALUES (%s, %s, now(), %s)
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value,
                 updated_at = now(), updated_by = EXCLUDED.updated_by""",
            (key, raw, user_id),
        )
        conn.execute(
            "INSERT INTO app_settings_audit (key, old_value, new_value, changed_by) VALUES (%s,%s,%s,%s)",
            (key, old["value"] if old else None, raw, user_id),
        )
        conn.commit()


def all_settings(prefix: str = "") -> dict[str, Any]:
    """Все настройки, опционально с фильтром по префиксу ключа."""
    q = "SELECT key, value FROM app_settings"
    args: tuple = ()
    if prefix:
        q += " WHERE key LIKE %s"
        args = (prefix + "%",)
    with pool.connection() as conn:
        rows = conn.execute(q, args).fetchall()
    out = {}
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value"])
        except (ValueError, TypeError):
            out[r["key"]] = r["value"]
    return out


# ---------------- Пользователи ----------------

def hash_password(password: str) -> str:
    """Хеш пароля bcrypt."""
    import bcrypt
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Проверка пароля по хешу."""
    import bcrypt
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_user(email: str, password: str, full_name: str = "", role: str = "customer",
                phone: str = "") -> int:
    """Создать пользователя, вернуть id.

    Телефон важен не меньше пароля: по нему гостевые заказы (оформленные без аккаунта)
    привязываются к клиенту — вместе с загруженными фото.
    """
    with pool.connection() as conn:
        row = conn.execute(
            """INSERT INTO users (email, password_hash, full_name, role, phone)
               VALUES (lower(%s), %s, %s, %s, %s) RETURNING id""",
            (email, hash_password(password), full_name, role, phone or None),
        ).fetchone()
        conn.commit()
    return row["id"]


def find_user_by_email(email: str) -> dict | None:
    """Найти пользователя по email."""
    with pool.connection() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE lower(email) = lower(%s) AND is_active", (email,)
        ).fetchone()


def get_user(user_id: int) -> dict | None:
    """Пользователь по id."""
    with pool.connection() as conn:
        return conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()


# ---------------- Сессии ----------------

def _hash_token(token: str) -> str:
    """В базе хранится только хеш токена — утечка БД не даёт войти."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user_id: int, days: int = 30, ua: str = "", ip: str = "") -> str:
    """Создать сессию, вернуть открытый токен (в БД уходит только хеш)."""
    token = secrets.token_urlsafe(32)
    exp = datetime.now(timezone.utc) + timedelta(days=days)
    with pool.connection() as conn:
        conn.execute(
            """INSERT INTO sessions (token_hash, user_id, expires_at, user_agent, ip)
               VALUES (%s, %s, %s, %s, %s)""",
            (_hash_token(token), user_id, exp, ua[:300], ip[:64]),
        )
        conn.commit()
    return token


def session_user(token: str) -> dict | None:
    """Пользователь по токену сессии (если сессия жива)."""
    if not token:
        return None
    with pool.connection() as conn:
        return conn.execute(
            """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = %s AND s.expires_at > now() AND u.is_active""",
            (_hash_token(token),),
        ).fetchone()


def drop_session(token: str) -> None:
    """Удалить сессию (выход)."""
    with pool.connection() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = %s", (_hash_token(token),))
        conn.commit()
