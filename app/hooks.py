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

from fastapi import APIRouter, Form, Request
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


def incoming(channel: str, chat_id: str, author: str, body: str, external_id: str = "",
             title: str = "", meta: dict | None = None) -> int:
    """Положить входящее сообщение в инбокс. Возвращает id диалога.

    Публичное имя: кроме вебхуков сюда пишет приёмник Telegram (tg_receiver),
    чтобы разбор сообщений жил в одном месте.
    """
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
    handle_tg_update(data)
    return JSONResponse({"ok": True})


def handle_tg_update(update: dict) -> int:
    """Разобрать апдейт Telegram и положить сообщение в инбокс. Возвращает update_id.

    Один код для двух способов доставки: вебхук (POST /hooks/telegram/<secret>)
    и long polling (tg_receiver). Служебные события без чата молча пропускаются:
    Telegram ждёт ответа «получил», но писать в инбокс нечего.
    """
    msg = (update or {}).get("message") or (update or {}).get("edited_message") or {}
    chat = msg.get("chat") or {}
    if not chat:
        return int((update or {}).get("update_id") or 0)
    frm = msg.get("from") or {}
    name = " ".join(x for x in [frm.get("first_name"), frm.get("last_name")] if x) \
        or frm.get("username") or "Гость"
    incoming(
        channel="telegram",
        chat_id=str(chat.get("id")),
        author=name,
        body=msg.get("text") or msg.get("caption") or "",
        external_id=str(msg.get("message_id", "")),
        title=f"Telegram: {name}",
        meta={"username": frm.get("username"), "chat_type": chat.get("type")},
    )
    return int((update or {}).get("update_id") or 0)


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


def _hook_landing(request: Request, channel: str = ""):
    """Адрес вебхука открыли глазами — ведём на единственную страницу настроек приёма.

    Держим ОДНУ страницу на тему: /admin/hooks. Вторая «объяснялка» только
    путала владельца — две страницы про одно и то же.
    """
    user = db.session_user(request.cookies.get("sid", ""))
    if not (user and user.get("role") == "admin"):
        return RedirectResponse("/login?next=" + quote(request.url.path), status_code=303)
    return RedirectResponse("/admin/hooks", status_code=303)


@router.get("/hooks")
@router.get("/hooks/")
def hooks_alias(request: Request):
    """Старый адрес страницы приёма — на /admin/hooks."""
    return _hook_landing(request)


@router.get("/admin/hooks")
@router.get("/admin/hooks/")
def hooks_root(request: Request):
    """Адрес вебхуков открывают случайно — показываем страницу, а не служебный JSON.

    Залогиненному админу показываем статус каналов, остальных ведём на вход.
    """
    user = db.session_user(request.cookies.get("sid", ""))
    if not (user and user.get("role") == "admin"):
        return RedirectResponse("/login?next=" + quote(request.url.path), status_code=303)
    return render(request, "hooks_help.html", user=user, **_hook_state(request))


def _hook_state(request) -> dict:
    """Состояние каналов для страницы приёма. Секреты — только маской."""
    tg_raw = secrets_helper.decrypt(str(db.get_setting("telegram.token", "") or ""))
    vk_raw = secrets_helper.decrypt(str(db.get_setting("vk.token", "") or ""))
    hook_secret = secrets_helper.decrypt(str(db.get_setting("telegram.hook_secret", "") or ""))
    site = str(db.get_setting("site.url", "") or "https://alyaeva.pro").rstrip("/")
    return {
        "settings": {k: v for k, v in db.all_settings("telegram.").items()},
        "tg_ready": bool(tg_raw), "vk_ready": bool(vk_raw),
        # «vk_events_ready»: ключ сообщества с обычными правами НЕ умеет ни Callback API,
        # ни опрос (VK: ошибка 15 «no access to call this method with current scopes»).
        # Считаем события настроенными, только если задана строка подтверждения —
        # её можно получить лишь при подключённом Callback API. Иначе честно говорим
        # «события не приходят» и объясняем, что делать (замер 26.11.2026).
        "vk_events_ready": bool(str(db.get_setting("vk.confirm_code", "") or "").strip()),
        "tg_token_mask": _mask(tg_raw), "vk_token_mask": _mask(vk_raw),
        "hook_secret": hook_secret, "site_url": site,
        "vk_confirm_set": bool(str(db.get_setting("vk.confirm_code", "") or "").strip()),
        "polling": str(db.get_setting("telegram.mode", "") or "") == "polling",
        "poll_last_at": str(db.get_setting("telegram.poll.last_at", "") or ""),
        "saved": request.query_params.get("saved") == "1",
    }


def _mask(secret: str) -> str:
    """Показать факт наличия ключа, не показывая ключ."""
    if not secret:
        return "не задан"
    return "задан ···· " + secret[-4:] if len(secret) > 4 else "задан"


@router.post("/admin/hooks/receive-telegram")
def hooks_enable_receive(request: Request):
    """Включить приём сообщений Telegram опросом (кнопка на странице приёма).

    Вебхук не доходит: провайдер рвёт входящие соединения с зарубежных адресов
    (задача #285). Опрос идёт исходяще, через прокси — включаем его.
    """
    user = db.session_user(request.cookies.get("sid", ""))
    if not (user and user.get("role") == "admin"):
        return JSONResponse({"ok": False, "error": "Нужно войти"}, status_code=401)
    from tg_receiver import enable_polling
    return JSONResponse(enable_polling())


@router.post("/admin/hooks")
def hooks_save(request: Request, telegram_token: str = Form(""), telegram_proxy: str = Form(""),
               telegram_chat: str = Form(""), telegram_channel: str = Form(""),
               vk_token: str = Form(""), vk_group: str = Form(""),
               vk_confirm_code: str = Form(""), vk_secret: str = Form("")):
    """Сохранение подключения мессенджеров. Пустое поле — не менять прежнее значение.

    Ошибка 26.11.2026: пустое «Прокси» или «Чат» стирало настроенное — владелец
    заполнял только токен VK, а форма затирала прокси Telegram, и отправка
    ломалась «сама по себе». Теперь все поля ведут себя как токены: пустое не трогаем.
    """
    user = db.session_user(request.cookies.get("sid", ""))
    if not (user and user.get("role") == "admin"):
        return RedirectResponse("/login?next=" + quote("/admin/hooks"), status_code=303)
    if telegram_token.strip():
        db.set_setting("telegram.token", secrets_helper.encrypt(telegram_token.strip()), user["id"])
        # Секрет адреса вебхука: без него кто угодно мог бы слать нам «сообщения».
        if not db.get_setting("telegram.hook_secret"):
            import secrets as _secrets
            db.set_setting("telegram.hook_secret",
                           secrets_helper.encrypt(_secrets.token_urlsafe(32)), user["id"])
    if telegram_proxy.strip():
        db.set_setting("telegram.proxy", telegram_proxy.strip(), user["id"])
    if telegram_chat.strip():
        db.set_setting("telegram.chat", telegram_chat.strip(), user["id"])
    # Куда публиковать посты: без этих адресов кнопка «Опубликовать» на /admin/posts
    # всегда погашена — раньше их нельзя было задать вообще нигде (задача #286).
    if telegram_channel.strip():
        db.set_setting("telegram.channel", telegram_channel.strip(), user["id"])
    if vk_token.strip():
        db.set_setting("vk.token", secrets_helper.encrypt(vk_token.strip()), user["id"])
    if vk_group.strip():
        db.set_setting("vk.group", vk_group.strip(), user["id"])
    # Строка подтверждения и секрет событий VK — не токены доступа: без шифрования.
    # Нужны для Callback API: VK шлёт подтверждение, сервер должен вернуть ту же строку.
    if vk_confirm_code.strip():
        db.set_setting("vk.confirm_code", vk_confirm_code.strip(), user["id"])
    if vk_secret.strip():
        db.set_setting("vk.secret", vk_secret.strip(), user["id"])
    # Ответ без перезагрузки: форма шлёт Accept: application/json и показывает плашку на месте (#217).
    wants_json = "application/json" in (request.headers.get("accept") or "")
    if wants_json:
        return JSONResponse({"ok": True, "saved": "подключение мессенджеров"})
    return RedirectResponse("/admin/hooks?saved=1", status_code=303)


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


@router.get("/hooks/status")  # для скриптов, не для людей
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
