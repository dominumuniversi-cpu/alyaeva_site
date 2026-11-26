"""Отправка сообщений в мессенджеры. Настройки — из БД, не из кода.

Telegram: Bot API через прокси (в РФ API заблокирован провайдером).
VK: messages.send от имени сообщества.
Токены берутся из app_settings и расшифровываются на лету.
"""
from __future__ import annotations

import logging

import httpx

import db
import secrets_helper

log = logging.getLogger("alyaeva.messaging")


def _telegram_http() -> httpx.Client:
    """HTTP-клиент Telegram с прокси из настроек."""
    proxy = str(db.get_setting("telegram.proxy", "") or "").strip()
    return httpx.Client(timeout=20, proxy=proxy or None)


def tg_api(method: str, params: dict | None = None, http_timeout: float = 20) -> dict:
    """Вызов метода Telegram Bot API через прокси из настроек.

    Возвращает разобранный JSON. Ошибки сети не бросает, а возвращает
    {"ok": False, "error": ...}: вызывающий код (кнопка админки, приёмник)
    решает, что считать отказом и что показать человеку.
    """
    token = secrets_helper.decrypt(str(db.get_setting("telegram.token", "") or ""))
    if not token:
        return {"ok": False, "error": "не задан токен бота"}
    proxy = str(db.get_setting("telegram.proxy", "") or "").strip() or None
    try:
        with httpx.Client(timeout=http_timeout, proxy=proxy) as client:
            # POST JSON, а НЕ GET с параметрами (исправлено 26.11.2026, #295):
            # Telegram Bot API принимает аргументы метода только в теле JSON —
            # на GET он отвечает 404 «Not Found» / игнорирует параметры, и такие
            # методы, как answerCallbackQuery, молча не работали.
            r = client.post(f"https://api.telegram.org/bot{token}/{method}", json=params or {})
            return r.json()
    except Exception as e:
        log.warning("telegram api %s: %s", method, e)
        return {"ok": False, "error": str(e)[:160]}


#: Почему последняя отправка не удалась — для страницы «Посты».
#: Раньше в базу писалось просто "fail", и причину нельзя было узнать ни человеку, ни агенту
#: (владелец: «ни в одном канале не опубликован — почему?», 26.11.2026).
last_error: dict[str, str] = {}


def send_telegram(chat_id: str, text: str) -> bool:
    """Отправить сообщение в Telegram."""
    token = secrets_helper.decrypt(str(db.get_setting("telegram.token", "") or ""))
    if not token:
        last_error["telegram"] = "не задан токен бота"
        log.info("telegram: не настроен токен или чат")
        return False
    if not chat_id:
        last_error["telegram"] = "не указан канал для публикаций (страница «Интеграция»)"
        log.info("telegram: не настроен токен или чат")
        return False
    try:
        with _telegram_http() as client:
            r = client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            )
        ok = r.status_code == 200 and r.json().get("ok", False)
        if not ok:
            try:
                desc = str(r.json().get("description") or "")[:120]
            except Exception:  # noqa: BLE001
                desc = r.text[:120]
            last_error["telegram"] = f"Telegram ответил: {desc}"
            log.warning("telegram send failed: %s", r.text[:200])
        return ok
    except Exception as e:
        last_error["telegram"] = f"нет связи с Telegram: {str(e)[:100]}"
        log.warning("telegram error: %s", e)
        return False


def send_vk(peer_id: str, text: str) -> bool:
    """Отправить сообщение во ВКонтакте от имени сообщества.

    ВКонтакте разрешает сообществу писать только тем, кто написал ему первым.
    Ошибка 901 — нет разрешения, 945 — сообщения сообщества выключены.
    """
    token = secrets_helper.decrypt(str(db.get_setting("vk.token", "") or ""))
    if not token:
        last_error["vk"] = "не задан токен сообщества"
        log.info("vk: не настроен токен или получатель")
        return False
    if not peer_id:
        last_error["vk"] = "не указано сообщество для публикаций (страница «Интеграция»)"
        log.info("vk: не настроен токен или получатель")
        return False
    try:
        with httpx.Client(timeout=20) as client:
            r = client.post(
                "https://api.vk.com/method/messages.send",
                data={"user_id": peer_id, "message": text, "random_id": 0,
                      "access_token": token, "v": "5.199"},
            )
        data = r.json()
        if "error" in data:
            err = data["error"] or {}
            code = err.get("error_code")
            msg_vk = str(err.get("error_msg") or "")[:120]
            last_error["vk"] = f"ВКонтакте ответил {code}: {msg_vk}"
            log.warning("vk error: %s", msg_vk)
            return False
        return True
    except Exception as e:
        last_error["vk"] = f"нет связи с ВКонтакте: {str(e)[:100]}"
        log.warning("vk error: %s", e)
        return False


def deliver(thread: dict, text: str) -> bool:
    """Доставить ответ в канал диалога."""
    channel = thread.get("channel")
    target = thread.get("external_chat_id") or ""
    if channel == "telegram":
        return send_telegram(target, text)
    if channel == "vk":
        return send_vk(target, text)
    return False
