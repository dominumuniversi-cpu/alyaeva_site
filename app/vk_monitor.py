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


def _self_peer() -> str:
    """Кому отправить пробное сообщение: СЕБЕ (получатель сервисного ключа).

    Почему не в сообщество (проверено 26.11.2026, прод): `peer_id` равный id сообщества
    даёт 901 «Can't send messages for users without permission» всегда — у сообщества нет
    переписки с самим собой, и ошибка ничего не говорит о праве писать людям. Человек видел
    «отправка не разрешена» и делал вывод, что сломан весь VK.
    """
    import db as _db

    return str(_db.get_setting("vk.self_peer", "") or "").strip()


def _send_state(group_id: int | None) -> dict:
    """Можно ли писать от имени сообщества.

    Если получатель проверки сохранён (vk.self_peer) — пишем ему: это настоящая проверка.
    Если нет — честно говорим «не проверяли», а не пугаем человека ошибкой 901.
    """
    if not group_id:
        return {"ok": None, "code": 0, "message": "нет сообщества", "checked": False}
    peer = _self_peer()
    if not peer:
        return {"ok": None, "code": 0, "checked": False,
                "message": "Проверка отправки не настроена: сохраните свой получатель в поле «Кому слать проверку»."}
    d = _call("messages.send", {"user_id": peer, "message": "Проверка связи с сайтом alyaeva.pro",
                                "random_id": 0})
    code, msg = _err(d)
    return {"ok": code == 0, "code": code, "message": msg, "checked": True}


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
    # 901 = «нельзя писать этому человеку». Советы разные для разных кодов:
    # 901/902 — клиент не писал сообществу или закрыл личные сообщения;
    # 945 — сообщения сообщества вообще выключены в настройках.
    if send.get("checked") and send.get("ok") is False:
        if send.get("code") == 945:
            steps.append("Сообщество → «Управление» → «Сообщения» → включите «Сообщения сообщества».")
        elif send.get("code") in (901, 902):
            steps.append("Напишите сообществу со своего аккаунта одно слово и нажмите «Проверить связь» "
                         "ещё раз: ВКонтакте разрешает сообществу отвечать только тем, кто писал первым.")
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
