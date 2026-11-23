"""Заказы, проекты, библиотека фото и шлюз сообщений.

Структура библиотеки (решение владельца): клиент → проекты (папки) → фото.
Каждый проект привязан к заказу или создан вручную. Доступ: владелец и админ.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import unicodedata
from datetime import datetime, timezone

import db
from config import config

ALLOWED_IMAGE = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif", ".bmp", ".tif", ".tiff"}
MAX_SIDE = 4000  # пикселей по большей стороне после уменьшения


def next_order_number() -> str:
    """Человекочитаемый номер заказа: AA-ГГММДД-NNN."""
    today = datetime.now(timezone.utc).strftime("%y%m%d")
    with db.pool.connection() as conn:
        row = conn.execute(
            "SELECT count(*) AS c FROM orders WHERE number LIKE %s", ("AA-" + today + "%",)
        ).fetchone()
    return f"AA-{today}-{row['c'] + 1:03d}"


def create_order(user_id: int | None, name: str, phone: str, email: str, comment: str,
                 items: list[dict]) -> dict:
    """Создать заказ с позициями, вернуть запись."""
    number = next_order_number()
    total = sum(float(i.get("price_rub", 0)) * int(i.get("qty", 1)) for i in items)
    with db.pool.connection() as conn:
        order = conn.execute(
            """INSERT INTO orders (number, user_id, customer_name, customer_phone, customer_email,
                                   comment, total_rub, consent_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s, now()) RETURNING *""",
            (number, user_id, name, phone, email, comment, total),
        ).fetchone()
        for it in items:
            conn.execute(
                """INSERT INTO order_items (order_id, product_id, title, price_rub, qty)
                   VALUES (%s,%s,%s,%s,%s)""",
                (order["id"], it.get("product_id"), it.get("title", ""),
                 float(it.get("price_rub", 0)), int(it.get("qty", 1))),
            )
        conn.commit()
    return order


def set_order_status(order_id: int, status: str) -> None:
    """Сменить статус заказа."""
    with db.pool.connection() as conn:
        conn.execute("UPDATE orders SET status = %s, updated_at = now() WHERE id = %s", (status, order_id))
        conn.commit()


def user_orders(user_id: int) -> list[dict]:
    """Заказы пользователя."""
    with db.pool.connection() as conn:
        return conn.execute(
            "SELECT * FROM orders WHERE user_id = %s ORDER BY created_at DESC", (user_id,)
        ).fetchall()


def all_orders(limit: int = 200) -> list[dict]:
    """Все заказы для админки."""
    with db.pool.connection() as conn:
        return conn.execute(
            """SELECT o.*, u.email AS user_email,
                 (SELECT count(*) FROM projects p WHERE p.order_id = o.id) AS projects
               FROM orders o LEFT JOIN users u ON u.id = o.user_id
               ORDER BY o.created_at DESC LIMIT %s""", (limit,)
        ).fetchall()


# ---------- Проекты и файлы ----------

def create_project(user_id: int, title: str, order_id: int | None = None, note: str = "") -> int:
    """Создать папку-проект у клиента."""
    with db.pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO projects (user_id, order_id, title, note) VALUES (%s,%s,%s,%s) RETURNING id",
            (user_id, order_id, title, note),
        ).fetchone()
        conn.commit()
    return row["id"]


def user_projects(user_id: int) -> list[dict]:
    """Проекты клиента с числом файлов."""
    with db.pool.connection() as conn:
        return conn.execute(
            """SELECT p.*, (SELECT count(*) FROM project_files f WHERE f.project_id = p.id) AS files
               FROM projects p WHERE p.user_id = %s ORDER BY p.created_at DESC""", (user_id,)
        ).fetchall()


def project_files(project_id: int, user_id: int | None, is_admin: bool) -> list[dict]:
    """Файлы проекта. Клиент видит только свои, админ — любые."""
    with db.pool.connection() as conn:
        if is_admin:
            return conn.execute(
                "SELECT * FROM project_files WHERE project_id = %s ORDER BY created_at", (project_id,)
            ).fetchall()
        return conn.execute(
            "SELECT * FROM project_files WHERE project_id = %s AND user_id = %s ORDER BY created_at",
            (project_id, user_id),
        ).fetchall()


def _safe_name(name: str) -> str:
    """Безопасное имя файла (без пути и спецсимволов)."""
    base = os.path.basename(name)
    base = unicodedata.normalize("NFKD", base)
    base = re.sub(r"[^\w.\-]+", "_", base, flags=re.UNICODE)
    return base[:120] or "file"


def project_for_order(order_row: dict) -> int:
    """Проект для заказа. Один проект на заказ — чтобы фото не смешивались между заказами.

    Клиент видит свои проекты по user_id. Гость (user_id=None) — проект без владельца,
    привязывается к клиенту при регистрации по тому же телефону.
    """
    with db.pool.connection() as conn:
        existing = conn.execute(
            "SELECT id FROM projects WHERE order_id = %s LIMIT 1", (order_row["id"],)).fetchone()
        if existing:
            return existing["id"]
        row = conn.execute(
            """INSERT INTO projects (user_id, order_id, title, note)
               VALUES (%s,%s,%s,%s) RETURNING id""",
            (order_row.get("user_id"), order_row["id"],
             f"Заказ {order_row['number']}",
             order_row.get("customer_name") or "")).fetchone()
        return row["id"]


def save_upload(project_id: int, user_id: int | None, filename: str, content: bytes) -> dict | None:
    """Сохранить фото в проект. Возвращает запись или None, если тип не разрешён."""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_IMAGE:
        return None
    if len(content) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise ValueError("Файл слишком большой")
    stored = secrets.token_hex(16) + ext
    # Папка — по проекту, без привязки к владельцу: гость может стать клиентом,
    # и файл не должен из-за этого «переезжать».
    rel_path = os.path.join("guest", str(project_id), stored)
    dest_dir = os.path.join(config.UPLOAD_DIR, "guest", str(project_id))
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, stored)
    with open(path, "wb") as f:
        f.write(content)
    with db.pool.connection() as conn:
        row = conn.execute(
            """INSERT INTO project_files (project_id, user_id, orig_name, stored_name, size_bytes, rel_path)
               VALUES (%s,%s,%s,%s,%s,%s) RETURNING *""",
            (project_id, user_id, _safe_name(filename), stored, len(content), rel_path),
        ).fetchone()
        conn.commit()
    return row


def file_path(row: dict) -> str:
    """Полный путь к файлу на диске.

    Берём сохранённый rel_path, а если его нет (старые записи) — собираем из проекта.
    Путь НЕ зависит от user_id: иначе привязка гостевого заказа к аккаунту ломала бы ссылку.
    """
    rel = row.get("rel_path") if isinstance(row, dict) else None
    if not rel:
        rel = os.path.join("guest", str(row["project_id"]), row["stored_name"])
    return os.path.join(config.UPLOAD_DIR, rel)


# ---------- Шлюз сообщений ----------

def log_message(channel: str, direction: str, body: str, author: str = "",
                thread_id: int | None = None, external_id: str | None = None,
                meta: str = "{}") -> int:
    """Записать сообщение в единый шлюз."""
    with db.pool.connection() as conn:
        row = conn.execute(
            """INSERT INTO message_hub (thread_id, direction, channel, external_id, author, body, meta)
               VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (thread_id, direction, channel, external_id, author, body, meta),
        ).fetchone()
        if thread_id:
            conn.execute("UPDATE message_threads SET last_at = now() WHERE id = %s", (thread_id,))
        conn.commit()
    return row["id"]


def threads(limit: int = 100) -> list[dict]:
    """Диалоги для единого окна общения."""
    with db.pool.connection() as conn:
        return conn.execute(
            """SELECT t.*, 
                 (SELECT body FROM message_hub m WHERE m.thread_id = t.id ORDER BY m.id DESC LIMIT 1) AS last_body,
                 (SELECT count(*) FROM message_hub m WHERE m.thread_id = t.id) AS msgs
               FROM message_threads t ORDER BY t.last_at DESC LIMIT %s""", (limit,)
        ).fetchall()


def thread_messages(thread_id: int) -> list[dict]:
    """История сообщений диалога."""
    with db.pool.connection() as conn:
        return conn.execute(
            "SELECT * FROM message_hub WHERE thread_id = %s ORDER BY id", (thread_id,)
        ).fetchall()


def claim_guest_orders(user_id: int, phone: str, email: str = "") -> int:
    """Забрать гостевые заказы клиента при регистрации.

    Гость оформлял заказ без аккаунта — заказ лежит без владельца, а фото в папке guest/.
    Когда человек регистрируется с тем же телефоном, заказы и их фото становятся его.
    Возвращает, сколько заказов присвоено.
    """
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if not digits:
        return 0
    with db.pool.connection() as conn:
        # Сравниваем только цифры: +7 902 875-93-87 и 89028759387 — один и тот же человек.
        rows = conn.execute(
            """SELECT id FROM orders
               WHERE user_id IS NULL
                 AND regexp_replace(coalesce(customer_phone,''), '\\D', '', 'g') LIKE %s""",
            ("%" + digits[-10:],),
        ).fetchall()
        ids = [r["id"] for r in rows]
        if not ids:
            return 0
        conn.execute("UPDATE orders SET user_id = %s WHERE id = ANY(%s)", (user_id, ids))
        # Проекты этих заказов тоже переводим на клиента — фото уходят из guest/ в его папку.
        conn.execute(
            """UPDATE projects SET user_id = %s
               WHERE order_id = ANY(%s) AND (user_id IS NULL OR user_id = 0)""",
            (user_id, ids))
        conn.execute("UPDATE project_files SET user_id = %s WHERE project_id IN (SELECT id FROM projects WHERE order_id = ANY(%s))",
                     (user_id, ids))
        return len(ids)
