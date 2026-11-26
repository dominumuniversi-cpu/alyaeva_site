"""Фоновый фоновый цикл завода: раз в минуту отправить посты, которым пора.

Зачем отдельный поток: расписание без исполнителя — это поле «когда», которое никто не читает.
Поток тот же приём, что у приёма сообщений Telegram (tg_receiver): демон, стартует вместе
с приложением, сам проверяет, что делать, и молча ждёт, если делать нечего.

Почему не планировщик (APScheduler, Celery): лишняя зависимость и лишний процесс в контейнере
ради одной проверки раз в минуту. Здесь — один поток и один SELECT.
"""
from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger("alyaeva.cf.worker")

_started = False
_lock = threading.Lock()

#: Как часто проверять очередь. 30 секунд — расписание точно в минуту, лишнего запроса нет.
TICK = 30


def run_once() -> list[dict]:
    """Один проход: что пора — отправить. Возвращается итог для теста и лога."""
    from . import queue
    try:
        got = queue.send_due(10)
    except Exception as e:  # noqa: BLE001 — фон не имеет права ронять приложение
        log.warning("контент-завод: проход не удался: %s", e)
        return []
    for r in got:
        log.info("контент-завод: пост %s — %s", r["id"], r["summary"])
    return got


def _loop() -> None:
    """Вечный цикл со сном. Ошибки — в лог и дальше спать: одна неудача не останавливает завод."""
    while True:
        try:
            run_once()
        except Exception as e:  # noqa: BLE001
            log.warning("контент-завод: цикл: %s", e)
        time.sleep(TICK)


def start() -> bool:
    """Запустить поток один раз. Второй вызов — ничего не делает, возвращает False."""
    global _started
    if os.getenv("CF_WORKER", "1") == "0":
        return False
    with _lock:
        if _started:
            return False
        threading.Thread(target=_loop, name="cf-worker", daemon=True).start()
        _started = True
        log.info("контент-завод: фоновый исполнитель запущен")
        return True
