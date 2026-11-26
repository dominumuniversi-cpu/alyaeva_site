"""Напоминания клиенту, которые никто не нажимает вручную.

Зачем: заказ без фотографий стоит на месте, а человек об этом не знает —
он загрузил анкету, закрыл вкладку и ждёт. Через три дня получает уведомление
на сайте: «по заказу такому-то не хватает фото».

Как запускается: при старте приложения (lifespan) в фоновом потоке раз в час.
Повторно не напоминаем — в уведомлениях ищем уже отправленное по этому заказу.
"""
from __future__ import annotations

import logging
import threading
import time

import db
from notifications import notify

log = logging.getLogger("reminders")
# uvicorn настраивает только свои логгеры: без этого наши сообщения не попадают в docker logs.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# ТОКЕН НЕ В ЛОГАХ (26.11.2026). На INFO httpx печатает каждый запрос целым URL,
# а адрес Bot API содержит токен: /bot<id>:<секрет>/getUpdates. В docker logs прода
# токен светился открытым текстом. httpx на INFO не нужен никогда — приглушаем его
# отдельно, остальные наши логгеры не трогаем.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Через сколько дней молчания заказа без фото напоминаем.
DAYS_WITHOUT_PHOTOS = 3
# Как часто проверяем. Час — потому что счёт идёт на дни, чаще незачем.
CHECK_EVERY_SECONDS = 3600


def remind_missing_photos() -> int:
    """Разослать напоминания по заказам без фото. Возвращает, сколько отправлено."""
    sent = 0
    with db.pool.connection() as conn:
        rows = conn.execute(
            """SELECT o.id, o.number, o.user_id
               FROM orders o
               -- До макета фото ещё ждут; после макета напоминать поздно — работа идёт.
               WHERE o.status IN ('new', 'in_work')
                 AND o.created_at < now() - make_interval(days => %s)
                 AND NOT EXISTS (SELECT 1 FROM projects p
                                 JOIN project_files f ON f.project_id = p.id
                                 WHERE p.order_id = o.id)
                 AND NOT EXISTS (SELECT 1 FROM notifications n
                                 WHERE n.user_id = o.user_id
                                   AND n.kind = 'photos_reminder'
                                   AND n.url = '/cabinet/order/' || o.id)
            """,
            (DAYS_WITHOUT_PHOTOS,),
        ).fetchall()
        for r in rows:
            notify(conn, r["user_id"], "photos_reminder",
                   "Не хватает фотографий по заказу " + str(r["number"]),
                   "Макет не начать без ваших фото — загрузить можно на странице заказа.",
                   "/cabinet/order/" + str(r["id"]))
            sent += 1
    if sent:
        log.info("напоминаний о фото: %s", sent)
    return sent


def _loop() -> None:
    """Фон: сразу проверка, дальше час сна. Ошибки не роняют приложение."""
    while True:
        try:
            remind_missing_photos()
        except Exception as e:  # noqa: BLE001 — фон не имеет права молча умереть
            log.warning("напоминания не отправлены: %s", e)
        time.sleep(CHECK_EVERY_SECONDS)


def start() -> None:
    """Запустить фоновую проверку один раз на процесс (поток-демон)."""
    t = threading.Thread(target=_loop, name="reminders", daemon=True)
    t.start()
    log.info("напоминания о фото запущены (проверка сразу, потом раз в час)")
