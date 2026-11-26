"""Приём сообщений Telegram в фоне — long polling.

Почему не вебхук: провайдер рвёт входящие соединения к нашему 443 с зарубежных
адресов. Проверено 26.11.2026: Telegram отвечает на доставку вебхука
«Connection timed out», а прямой POST с внешнего IP обрывается на TLS
(задача #285). Исходящие через прокси работают — поэтому приложение само
забирает новые сообщения у Telegram.

Настройки (app_settings):
- telegram.mode = "polling"      — приём включён;
- telegram.update_offset         — id последнего обработанного апдейта;
- telegram.poll.last_at          — время последнего успешного опроса (статус на странице).

Запускается при старте приложения (если режим включён) и кнопкой на /admin/hooks.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

import db
import messaging

log = logging.getLogger("alyaeva.tg_receiver")

# Долгий опрос: Telegram держит ответ открытым, пока не появится сообщение.
LONG_POLL_SECONDS = 25
# Пауза при ошибке (нет токена, прокси недоступен): не долбим API поминутно.
PAUSE_ON_ERROR_SECONDS = 30

_lock = threading.Lock()
_started = False


def _now_iso() -> str:
    """UTC-время для настройки telegram.poll.last_at — статус на странице приёма."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _drain_once() -> bool:
    """Одна итерация опроса: получить апдейты, разложить в инбокс, сместить offset.

    Возвращает True при успешном ответе Telegram (пусть и пустом). Ошибка — False,
    вызывающий код сделает паузу. Апдейты обрабатываются по одному: один кривой
    апдейт не должен останавливать остальные.
    """
    import hooks
    last = int(db.get_setting("telegram.update_offset", 0) or 0)
    r = messaging.tg_api("getUpdates",
                         params={"offset": last + 1, "timeout": LONG_POLL_SECONDS},
                         http_timeout=LONG_POLL_SECONDS + 15)
    if not r.get("ok"):
        return False
    updates = r.get("result") or []
    for u in updates:
        try:
            hooks.handle_tg_update(u)
            if int(u.get("update_id") or 0) > last:
                last = int(u["update_id"])
        except Exception as e:  # noqa: BLE001 — фон не имеет права упасть из-за одного апдейта
            log.warning("апдейт %s не разобран: %s", u.get("update_id"), e)
    if updates:
        db.set_setting("telegram.update_offset", last)
        log.info("получено сообщений Telegram: %s", len(updates))
    db.set_setting("telegram.poll.last_at", _now_iso())
    return True


def _loop() -> None:
    """Фоновый цикл: работает, пока включён режим polling и есть токен."""
    while True:
        mode = str(db.get_setting("telegram.mode", "") or "")
        has_token = bool(db.get_setting("telegram.token", "") or "")
        if mode != "polling" or not has_token:
            time.sleep(PAUSE_ON_ERROR_SECONDS)
            continue
        ok = False
        try:
            ok = _drain_once()
        except Exception as e:  # noqa: BLE001 — любая ошибка здесь — пауза, не падение
            log.warning("опрос Telegram: %s", e)
        if not ok:
            time.sleep(PAUSE_ON_ERROR_SECONDS)


def enable_polling() -> dict:
    """Включить приём опросом. Вызывается кнопкой на /admin/hooks.

    getMe — честная проверка токена и прокси: без этого кнопка «включала бы»
    приём, который ни разу не работал. Вебхук убираем без сброса очереди:
    сообщения, которые Telegram не смог доставить, вернутся опросом.
    """
    me = messaging.tg_api("getMe")
    if not me.get("ok"):
        why = str(me.get("error") or me.get("description") or "нет ответа")
        return {"ok": False, "error": "Telegram не отвечает: " + why[:140]}
    messaging.tg_api("deleteWebhook", http_timeout=20)
    db.set_setting("telegram.mode", "polling")
    db.set_setting("telegram.poll.last_at", _now_iso())
    start_if_needed()
    username = (me.get("result") or {}).get("username") or ""
    return {"ok": True, "me": username, "mode": "polling",
            "note": "Приём включён: сообщения начнут приходить в течение минуты"}


def start_if_needed() -> None:
    """Запустить фоновый поток один раз на процесс, если включён режим polling."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
        threading.Thread(target=_loop, name="tg-receiver", daemon=True).start()
        log.info("поток приёма Telegram запущен")
