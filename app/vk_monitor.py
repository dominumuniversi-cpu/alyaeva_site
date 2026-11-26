"""Проверка связи с ВКонтакте — почему сообщения не приходят и куда уходят ответы.

Зачем отдельный модуль (26.11.2026): владелец написал «С VK группы сообщения не приходят».
Ответ «включите событие» ничего не даёт, потому что у VK несколько независимых мест,
и каждое может молчать по своей причине. Здесь одна функция `report()`, которая
по очереди спрашивает сам VK и говорит ПРОСТЫМИ СЛОВАМИ, что сломано и что нажать.

Проверено на живом токене NeuroArt Studio (26.11.2026):
- токен сообщества рабочий (groups.getById отвечает);
- отправка сообщений ему НЕ разрешена: VK отвечает 901 «Can't send messages for users
  without permission» — это про события сообщества (Callback API / ключ сообщений),
  а не про сам ключ;
- Callback API и длинный опрос этим ключом недоступны: ошибка 15 «no access to call
  this method with current scopes».

Ничего не меняет: только читает. Кнопки действий — на странице «Интеграция».
"""
from __future__ import annotations

import logging

import httpx

import db
import secrets_helper

log = logging.getLogger("alyaeva.vk_monitor")

VK_API = "https://api.vk.com/method/"
VK_VERSION = "5.199"
TIMEOUT = 15


def _token() -> str:
    """Токен ВКонтакте из настроек (расшифровка только в памяти процесса)."""
    return secrets_helper.decrypt(str(db.get_setting("vk.token", "") or ""))


def _call(method: str, params: dict) -> dict:
    """Запрос к VK. Ошибки сети не бросаем: возвращаем {"error": {...}} в форме VK."""
    token = _token()
    if not token:
        return {"error": {"error_code": 0, "error_msg": "токен не задан"}}
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            r = client.post(VK_API + method, data={**params, "access_token": token, "v": VK_VERSION})
        return r.json()
    except Exception as e:  # noqa: BLE001 — проверка не имеет права уронить страницу
        log.warning("vk %s: %s", method, e)
        return {"error": {"error_code": 0, "error_msg": str(e)[:120]}}


def _err(data: dict) -> tuple[int, str]:
    """Код и текст ошибки VK (0 и пусто, если ошибки нет)."""
    e = data.get("error") or {}
    return int(e.get("error_code") or 0), str(e.get("error_msg") or "")


def _group() -> dict | None:
    """Какое сообщество у токена: id, имя, короткий адрес."""
    d = _call("groups.getById", {})
    groups = (d.get("response") or {}).get("groups") or []
    return groups[0] if groups else None


def _events_state(group_id: int | None) -> dict:
    """Ждут ли сообщения: включён ли Callback API или ключ-приёмник.

    Спрашиваем VK, а не гадаем: getCallbackConfirmationCode / getLongPollServer
    отвечают «нет прав», если событий у токена нет вообще.
    """
    if not group_id:
        return {"state": "нет сообщества", "callback": None, "longpoll": None}
    cb = _call("groups.getCallbackConfirmationCode", {"group_id": group_id})
    lp = _call("groups.getLongPollServer", {"group_id": group_id})
    cb_code, cb_msg = _err(cb)
    lp_code, lp_msg = _err(lp)
    if cb_code == 0 or lp_code == 0:
        state = "события доступны"
    elif cb_code == 15 or lp_code == 15:
        # 15 = no access: этому ключу вообще нельзя работать с событиями сообщества
        state = "у ключа нет прав на события"
    else:
        state = "не удалось получить"
    return {"state": state, "callback": cb_msg or None, "longpoll": lp_msg or None}


def _send_state(group_id: int | None) -> dict:
    """Можно ли отвечать клиентам от имени сообщества.

    Пробное сообщение уходит ВЛАДЕЛЬЦУ сообщества (id = id сообщества): так проверяем
    право отправки, не трогая чужих людей, и сразу видно результат глазами.
    Код 901 «Can't send messages for users without permission» означает именно
    отсутствие прав у сообщества — то есть сообщения к вам просто не приходят.
    """
    if not group_id:
        return {"ok": None, "code": 0, "message": "нет сообщества"}
    d = _call("messages.send", {"peer_id": group_id, "message": "Проверка связи с сайтом alyaeva.pro",
                                "random_id": 0})
    code, msg = _err(d)
    return {"ok": code == 0, "code": code, "message": msg}


def report() -> dict:
    """Короткий отчёт для страницы «Интеграция»: что работает, что нет и что делать."""
    if not _token():
        return {"token": False, "ok": False,
                "state": "токен не задан",
                "hint": "Впишите токен сообщества выше и сохраните.",
                "steps": []}
    group = _group()
    gid = int(group.get("id") or 0) if group else 0
    events = _events_state(gid or None)
    send = _send_state(gid or None)

    steps: list[str] = []
    if events["state"] == "у ключа нет прав на события":
        steps.append("Сообщество → «Управление» → «Работа с API» → «Callback API» → включить «Сообщения».")
    elif events["state"] == "не удалось получить":
        steps.append("Сообщество → «Работа с API»: проверьте, включены ли события «Сообщения».")
    if send.get("ok") is False and send.get("code") == 901:
        steps.append("Включите «Возможность сообщений сообщества»: это и есть причина, "
                     "по которой переписка не доходит и ответы не уходят.")
    if not group:
        steps.append("Токен не принадлежит сообществу — выдайте токен в разделе сообщества «Работа с API».")
    ok = bool(group) and events["state"] == "события доступны" and send.get("ok") is not False
    return {
        "token": True,
        "ok": ok,
        "group_id": gid,
        "group_name": (group or {}).get("name") or "",
        "group_screen": (group or {}).get("screen_name") or "",
        "events": events,
        "send": send,
        "steps": steps,
        "state": "работает" if ok else "молчит",
    }
