"""Уведомления покупателю о его заказах.

Зачем: смена статуса — это новость для человека, а не строка в базе.
Раньше он узнавал о готовности макета, только если сам зашёл в кабинет.
"""
from __future__ import annotations


def notify(conn, user_id: int, kind: str, title: str, body: str = "", url: str = "") -> None:
    """Положить уведомление. Пишется в той же транзакции, что и событие, породившее его."""
    if not user_id:
        return
    conn.execute(
        "INSERT INTO notifications (user_id, kind, title, body, url) VALUES (%s, %s, %s, %s, %s)",
        (user_id, kind, title, body, url),
    )


def unread_count(conn, user_id: int) -> int:
    """Сколько непрочитанных — для точки в шапке кабинета."""
    if not user_id:
        return 0
    row = conn.execute(
        "SELECT count(*) AS n FROM notifications WHERE user_id = %s AND NOT is_read", (user_id,)
    ).fetchone()
    return int(row["n"]) if row else 0


def list_for(conn, user_id: int, limit: int = 50) -> list[dict]:
    """Последние уведомления человека, новые сверху."""
    rows = conn.execute(
        "SELECT id, kind, title, body, url, is_read, created_at FROM notifications"
        " WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
        (user_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_read(conn, user_id: int, notif_id: int | None = None) -> None:
    """Отметить прочитанным одно уведомление или все сразу."""
    if notif_id:
        conn.execute("UPDATE notifications SET is_read = true WHERE id = %s AND user_id = %s",
                     (notif_id, user_id))
    else:
        conn.execute("UPDATE notifications SET is_read = true WHERE user_id = %s AND NOT is_read",
                     (user_id,))
