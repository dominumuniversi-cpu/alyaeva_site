"""Своя аналитика: сколько зашли, сколько открыли заказ, сколько заказали.

Принцип из задачи #22: не тащить тяжёлую аналитику и не следить за людьми.
Мы пишем СОБЫТИЕ без личности: ни IP, ни cookie, ни fingerprint. Только день,
что произошло, страница и общий источник перехода.
"""
import re

# Откуда пришёл: по Referer. Хватает грубой сортировки — задача «поиск или соцсети»,
# а не точный атрибут канала.
_SOURCES = [
    ("search", re.compile(r"(yandex|google|bing|duckduckgo|mail\.ru|rambler)\.", re.I)),
    ("telegram", re.compile(r"t\.me|telegram", re.I)),
    ("vk", re.compile(r"vk\.(com|ru)|vkontakte", re.I)),
    ("instagram", re.compile(r"instagram\.com", re.I)),
    ("whatsapp", re.compile(r"wa\.me|whatsapp", re.I)),
]


# Как называть источник человеку, а не кодом.
SOURCE_NAMES = {
    "search": "Поиск (Яндекс, Google)",
    "telegram": "Telegram",
    "vk": "ВКонтакте",
    "instagram": "Instagram",
    "whatsapp": "WhatsApp",
    "direct": "Зашли напрямую",
    "other": "Другие сайты",
}


def source_from_referer(referer: str) -> str:
    """Понятное название источника или 'direct', если человек пришёл напрямую."""
    referer = (referer or "").strip()
    if not referer:
        return "direct"
    for name, rx in _SOURCES:
        if rx.search(referer):
            return name
    return "other"


def track(kind: str, page: str = "", source: str = "") -> None:
    """Записать событие. Молча выходит при любой ошибке.

    Аналитика — украшение, а не смысл страницы: если запись не удалась,
    посетитель не должен этого заметить или получить 500.
    """
    try:
        import db
        with db.pool.connection() as conn:
            conn.execute(
                "INSERT INTO site_events (kind, page, source) VALUES (%s, %s, %s)",
                (kind, (page or "")[:120], (source or "")[:40]),
            )
    except Exception:
        pass


def summary(days: int = 30) -> dict:
    """Сводка за период: просмотры, заказы, конверсия, источники.

    Формат нарочно плоский — чтобы страница админки не городила огород.
    """
    import db
    out = {"views": 0, "order_open": 0, "order_created": 0, "register": 0,
           "by_source": [], "by_page": [], "by_day": [], "conversion": 0.0}
    try:
        with db.pool.connection() as conn:
            row = conn.execute(
                """SELECT
                     count(*) FILTER (WHERE kind = 'view')         AS views,
                     count(*) FILTER (WHERE kind = 'order_open')   AS order_open,
                     count(*) FILTER (WHERE kind = 'order_created') AS order_created,
                     count(*) FILTER (WHERE kind = 'register')     AS register
                   FROM site_events WHERE day > current_date - %s::int""",
                (days,),
            ).fetchone()
            out.update({k: row[k] for k in ("views", "order_open", "order_created", "register")})
            out["by_source"] = conn.execute(
                """SELECT coalesce(nullif(source, ''), 'direct') AS name, count(*) AS n
                   FROM site_events WHERE kind = 'view' AND day > current_date - %s::int
                   GROUP BY 1 ORDER BY n DESC LIMIT 10""",
                (days,),
            ).fetchall()
            out["by_page"] = conn.execute(
                """SELECT nullif(page, '') AS name, count(*) AS n
                   FROM site_events WHERE kind = 'view' AND day > current_date - %s::int
                   GROUP BY 1 ORDER BY n DESC LIMIT 10""",
                (days,),
            ).fetchall()
            out["by_day"] = conn.execute(
                """SELECT day, count(*) FILTER (WHERE kind = 'view') AS views,
                          count(*) FILTER (WHERE kind = 'order_created') AS orders
                   FROM site_events WHERE day > current_date - %s::int
                   GROUP BY day ORDER BY day""",
                (days,),
            ).fetchall()
    except Exception:
        return out

    # Конверсия считается от открытий страницы заказа: сколько из заинтересовавшихся дошли до заявки.
    base = out["order_open"] or out["views"]
    if base:
        out["conversion"] = round(out["order_created"] * 100.0 / base, 1)
    return out
