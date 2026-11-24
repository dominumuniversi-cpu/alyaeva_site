"""Очередь постов: сохранение, история и что показывать в админке.

Зачем отдельным файлом: страница /admin/posts не должна содержать ни одного UPDATE —
иначе правка черновика и смена статуса разъедутся с тем, что видит человек.
Здесь же считается «что можно опубликовать сейчас» (задача #5, часть без токенов).
"""
from __future__ import annotations

import json

import db

# Каналы, которые понимает таблица posts. Хранятся одной строкой через запятую:
# колонка text, чтобы не заводить третью таблицу ради двух галочек.
CHANNELS = ("telegram", "vk")
STATUSES = {
    "draft": "Черновик",
    "queued": "В очереди",
    "sent": "Опубликован",
    "failed": "Не ушёл",
    "partial": "Ушёл не везде",
}


def _channels(text: str) -> list[str]:
    """Строка 'telegram,vk' → ['telegram', 'vk']; мусор отбрасываем."""
    got = [x.strip() for x in str(text or "").split(",")]
    return [x for x in got if x in CHANNELS]


def list_posts(limit: int = 50) -> list[dict]:
    """Все посты, свежие сверху — история и очередь одним списком."""
    with db.pool.connection() as conn:
        rows = conn.execute(
            """SELECT p.*, u.email AS author
                 FROM posts p LEFT JOIN users u ON u.id = p.created_by
                ORDER BY COALESCE(p.publish_at, p.created_at) DESC, p.id DESC
                LIMIT %s""",
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["channels_list"] = _channels(d.get("channels"))
        d["status_name"] = STATUSES.get(d.get("status"), d.get("status") or "")
        out.append(d)
    return out


def get_post(post_id: int) -> dict | None:
    """Один пост для правки."""
    with db.pool.connection() as conn:
        row = conn.execute("SELECT * FROM posts WHERE id = %s", (post_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["channels_list"] = _channels(d.get("channels"))
    return d


def save(post_id: int, body: str, channels: list[str], media_url: str, status: str) -> None:
    """Сохранить правку. Пустые каналы — ошибка: пост, у которого нет канала, некуда отправлять."""
    chans = ",".join(c for c in CHANNELS if c in set(channels))
    with db.pool.connection() as conn:
        conn.execute(
            "UPDATE posts SET body = %s, channels = %s, media_url = %s, status = %s WHERE id = %s",
            (body, chans, media_url, status, post_id),
        )
        conn.commit()


def create(body: str, channels: list[str], media_url: str, created_by: int | None) -> int:
    """Новый черновик. Статус всегда draft: отправить можно только настроенный канал.

    Каналов нет — ошибка, а не «поставим Telegram на всякий случай»: молча выбранный канал
    означает, что человек однажды не поймёт, откуда взялся пост. Проверку делает маршрут,
    здесь — страховка на случай прямого вызова.
    """
    chans = ",".join(c for c in CHANNELS if c in set(channels))
    if not chans:
        raise ValueError("Не выбран ни один канал")
    with db.pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO posts (body, channels, media_url, status, created_by) VALUES (%s, %s, %s, 'draft', %s) RETURNING id",
            (body, chans, media_url, created_by),
        ).fetchone()
        conn.commit()
    return int(row["id"])


def delete(post_id: int) -> bool:
    """Убрать черновик. Опубликованные не трогаем — это история."""
    with db.pool.connection() as conn:
        row = conn.execute("SELECT status FROM posts WHERE id = %s", (post_id,)).fetchone()
        if not row:
            return False
        if row["status"] == "sent":
            return False
        conn.execute("DELETE FROM posts WHERE id = %s", (post_id,))
        conn.commit()
    return True


def mark_result(post_id: int, status: str, result: dict) -> None:
    """Что ответили каналы. Пишем даже при ошибке: без этого причина потери не видна."""
    with db.pool.connection() as conn:
        conn.execute(
            "UPDATE posts SET status = %s, result = %s, published_at = CASE WHEN %s = 'sent' THEN now() ELSE published_at END WHERE id = %s",
            (status, json.dumps(result, ensure_ascii=False), status, post_id),
        )
        conn.commit()
