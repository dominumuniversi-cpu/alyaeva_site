"""Приём сообщений из мессенджеров в единый инбокс.

Telegram: вебхук POST /hooks/telegram/<secret>
VK:       вебхук POST /hooks/vk  (подтверждение + события)

Секрет в адресе — иначе кто угодно может слать нам «сообщения».
Сообщения кладём в message_hub, диалог ищем/создаём в message_threads.
"""
from __future__ import annotations

import hmac
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

import db
import orders
import secrets_helper
from urllib.parse import quote
from web_common import render

log = logging.getLogger("alyaeva.hooks")

router = APIRouter()


def thread_for(channel: str, external_chat_id: str, title: str = "") -> int:
    """Найти или создать диалог по каналу и внешнему id чата."""
    with db.pool.connection() as conn:
        row = conn.execute(
            "SELECT id FROM message_threads WHERE channel = %s AND external_chat_id = %s",
            (channel, str(external_chat_id)),
        ).fetchone()
        if row:
            return row["id"]
        row = conn.execute(
            """INSERT INTO message_threads (channel, external_chat_id, title, last_at)
               VALUES (%s, %s, %s, now()) RETURNING id""",
            (channel, str(external_chat_id), title or str(external_chat_id)),
        ).fetchone()
        conn.commit()
        return row["id"]


def _incoming(channel: str, chat_id: str, author: str, body: str, external_id: str = "",
              title: str = "", meta: dict | None = None) -> int:
    """Положить входящее сообщение в инбокс. Возвращает id диалога."""
    if not (body or "").strip():
        return 0
    tid = thread_for(channel, chat_id, title)
    orders.log_message(channel, "in", body.strip(), author=author,
                       thread_id=tid, external_id=external_id,
                       meta=json.dumps(meta or {}, ensure_ascii=False))
    return tid


@router.post("/hooks/telegram/{secret}")
async def telegram_hook(secret: str, request: Request):
    """Telegram Bot API присылает сюда каждое сообщение боту.

    Секрет сверяем не на равенство, а через hmac.compare_digest —
    иначе по времени ответа его можно подобрать посимвольно.
    """
    expected = secrets_helper.decrypt(str(db.get_setting("telegram.hook_secret", "") or ""))
    if not expected or not hmac.compare_digest(secret.encode("utf-8"), expected.encode("utf-8")):
        log.warning("telegram hook: неверный секрет")
        return JSONResponse({"ok": False}, status_code=403)
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "не JSON"}, status_code=400)
    msg = data.get("message") or data.get("edited_message") or {}
    chat = msg.get("chat") or {}
    if not chat:
        return JSONResponse({"ok": True})  # служебное событие, отвечаем «получил»
    frm = msg.get("from") or {}
    name = " ".join(x for x in [frm.get("first_name"), frm.get("last_name")] if x) \
        or frm.get("username") or "Гость"
    _incoming(
        channel="telegram",
        chat_id=str(chat.get("id")),
        author=name,
        body=msg.get("text") or msg.get("caption") or "",
        external_id=str(msg.get("message_id", "")),
        title=f"Telegram: {name}",
        meta={"username": frm.get("username"), "chat_type": chat.get("type")},
    )
    return JSONResponse({"ok": True})


@router.post("/hooks/vk")
async def vk_hook(request: Request):
    """ВКонтакте: сначала подтверждение сервера, потом события.

    VK требует вернуть строку 'ok' в теле, иначе повторяет событие.
    """
    data = await request.json()
    # 1) подтверждение адреса сервера
    if data.get("type") == "confirmation":
        code = secrets_helper.decrypt(str(db.get_setting("vk.confirm_code", "") or ""))
        return PlainTextResponse(code or "ok", status_code=200)
    # 2) проверка секрета события
    expected = secrets_helper.decrypt(str(db.get_setting("vk.secret", "") or ""))
    if expected and not hmac.compare_digest(str(data.get("secret", "")).encode("utf-8"), expected.encode("utf-8")):
        log.warning("vk hook: неверный secret")
        return PlainTextResponse("forbidden", status_code=403)
    if data.get("type") == "message_new":
        obj = (data.get("object") or {}).get("message") or {}
        frm = obj.get("from_id")
        text = obj.get("text") or ""
        _incoming(
            channel="vk",
            chat_id=str(obj.get("peer_id") or frm or ""),
            author=f"VK {frm}",
            body=text,
            external_id=str(obj.get("id", "")),
            title=f"VK: {frm}",
            meta={"from_id": frm},
        )
    return PlainTextResponse("ok")


def _hook_landing(request: Request, channel: str):
    """Страница «вы открыли служебный адрес» — одинаковая для всех каналов."""
    user = db.session_user(request.cookies.get("sid", ""))
    if not (user and user.get("role") == "admin"):
        return RedirectResponse("/login?next=" + quote(request.url.path), status_code=303)
    return render(request, "hook_landing.html", user=user, channel=channel)


@router.get("/hooks")
@router.get("/hooks/")
def hooks_root(request: Request):
    """Адрес вебхуков открывают случайно — показываем страницу, а не служебный JSON.

    Залогиненному админу показываем статус каналов, остальных ведём на вход.
    """
    user = db.session_user(request.cookies.get("sid", ""))
    if user and user.get("role") == "admin":
        tg = bool(secrets_helper.decrypt(str(db.get_setting("telegram.token", "") or "")))
        vk = bool(secrets_helper.decrypt(str(db.get_setting("vk.token", "") or "")))
        return render(request, "hooks_help.html", user=user, tg_ready=tg, vk_ready=vk)
    return RedirectResponse("/login?next=" + quote(request.url.path), status_code=303)


@router.get("/hooks/telegram/{secret}")
def telegram_get(secret: str, request: Request):
    """Адрес вебхука открыли в браузере.

    Сюда шлёт только Telegram методом POST. Если человек открыл адрес глазами —
    показываем, что он на месте и что с ним делать, а не «Method Not Allowed».
    """
    return _hook_landing(request, "Telegram")


@router.get("/hooks/vk")
def vk_get(request: Request):
    """Адрес вебхука VK открыли в браузере. Приём идёт только методом POST."""
    return _hook_landing(request, "ВКонтакте")


@router.get("/hooks/status")
async def hooks_status(request: Request):
    """Готов ли приём: какие каналы настроены (без раскрытия секретов).

    Открытый в браузере адрес показываем страницей, а не JSON —
    правила общие для всех страниц сайта.
    """
    # */* приходит от скриптов и curl — им нужен JSON. Страница — только если
    # браузер явно просит html.
    if "text/html" in (request.headers.get("accept") or ""):
        return _hook_landing(request, "мессенджеры")
    tg = bool(secrets_helper.decrypt(str(db.get_setting("telegram.token", "") or "")))
    tg_hook = bool(secrets_helper.decrypt(str(db.get_setting("telegram.hook_secret", "") or "")))
    vk = bool(secrets_helper.decrypt(str(db.get_setting("vk.token", "") or "")))
    return JSONResponse({
        "telegram": {"token": tg, "hook_secret": tg_hook, "ready": tg and tg_hook},
        "vk": {"token": vk, "ready": vk},
        "site": {"ready": True},
    })
