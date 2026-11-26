"""Каналы отправки: одна функция на площадку — POST или GET к API этой площадки.

Граница модуля: канал НЕ читает настройки сайта сам. Ему передают готовый словарь
соединения (адрес, токен, куда публиковать) — поэтому один и тот же код работает
и в этом сайте, и в любом другом: разница только в том, кто заполнил словарь.

Возвращается всегда одинаково: {"ok": bool, "detail": "словами, что ответил сервис"}.
Причина отказа — словами, а не кодом: человек читает страницу, а не лог.

Отправка идёт через httpx синхронно и НЕ держит соединение с базой: вызывающий код
достаёт строку из базы, отпускает её и только потом идёт в сеть. Иначе медленный
ответ площадки занимал бы соединение из пула на всё время ожидания.
"""
from __future__ import annotations

import json
import os
from typing import Callable

import httpx

#: Таймаут на один вызов API. Больше — человек решит, что кнопка не работает;
#: меньше — площадка не успеет ответить, и пост «не ушёл» зря.
TIMEOUT = 25.0


def _http(proxy: str = "") -> httpx.Client:
    """Клиент httpx с прокси, если он задан. Прокси нужен Telegram: в РФ API заблокирован."""
    return httpx.Client(timeout=TIMEOUT, proxy=(proxy or None))


def _err(prefix: str, body: str) -> dict:
    """Единый вид отказа: первая часть — что именно, вторая — ответ сервиса словами."""
    return {"ok": False, "detail": (prefix + ": " + (body or "").strip())[:300].strip(": ")}


def send_telegram(conn: dict, text: str, media_url: str = "") -> dict:
    """Телеграм: фото с подписью, если есть картинка, иначе сообщение.

    Разметка — HTML: адаптер уже сделал из Markdown корректные теги. Если Telegram всё равно
    откажет из-за разметки (бывает с чужими символами), повторяем без разметки — пост важнее
    оформления: человек увидит текст, а не «не удалось».
    """
    token = (conn.get("token") or "").strip()
    chat = (conn.get("target") or "").strip()
    if not token or not chat:
        return {"ok": False, "detail": "не задан токен бота или канал"}
    base = "https://api.telegram.org/bot%s/" % token
    proxy = (conn.get("proxy") or "").strip()
    try:
        with _http(proxy) as client:
            if media_url:
                r = client.post(base + "sendPhoto", json={"chat_id": chat, "photo": media_url,
                                                          "caption": text, "parse_mode": "HTML"})
            else:
                r = client.post(base + "sendMessage", json={"chat_id": chat, "text": text,
                                                            "parse_mode": "HTML",
                                                            "disable_web_page_preview": False})
            data = r.json() if r.content else {}
            if data.get("ok"):
                return {"ok": True, "detail": "отправлено"}
            desc = str(data.get("description") or r.text)[:200]
            # Отказ из-за разметки — повторяем чистым текстом, без parse_mode.
            if "entit" in desc.lower() or "parse" in desc.lower():
                plain = _strip_tags(text)
                if media_url:
                    r2 = client.post(base + "sendPhoto", json={"chat_id": chat, "photo": media_url,
                                                               "caption": plain})
                else:
                    r2 = client.post(base + "sendMessage", json={"chat_id": chat, "text": plain})
                if r2.json().get("ok"):
                    return {"ok": True, "detail": "отправлено без разметки"}
            return {"ok": False, "detail": "Telegram ответил: " + desc}
    except Exception as e:  # noqa: BLE001 — отказ сети не должен ронять страницу
        return _err("нет связи с Telegram", str(e))


def _strip_tags(text: str) -> str:
    """HTML → текст для повтора без разметки."""
    import re
    return re.sub(r"<[^>]+>", "", text or "")


def send_vk(conn: dict, text: str, media_url: str = "") -> dict:
    """ВКонтакте: wall.post от имени сообщества.

    owner_id = -<id группы>, from_group=1 — иначе пост уйдёт от имени автора токена,
    а не от сообщества. ВК возвращает id записи: он сохраняется как отметка, куда ушло.
    """
    token = (conn.get("token") or "").strip()
    group = str(conn.get("target") or "").strip()
    if not token or not group:
        return {"ok": False, "detail": "не задан токен сообщества или id группы"}
    owner = group if group.startswith("-") else "-" + group.lstrip("-")
    data = {"owner_id": owner, "from_group": "1", "message": text, "access_token": token, "v": "5.199"}
    if media_url:
        data["attachments"] = media_url
    try:
        with _http() as client:
            r = client.post("https://api.vk.com/method/wall.post", data=data)
        j = r.json()
        if j.get("error"):
            e = j["error"]
            return {"ok": False, "detail": "ВКонтакте ответил %s: %s" % (e.get("error_code"), str(e.get("error_msg"))[:160])}
        pid = (j.get("response") or {}).get("post_id")
        return {"ok": True, "detail": "запись на стене" + (" №%s" % pid if pid else "")}
    except Exception as e:  # noqa: BLE001
        return _err("нет связи с ВКонтакте", str(e))


def send_ok(conn: dict, text: str, media_url: str = "") -> dict:
    """Одноклассники: mediatopic.post от имени группы.

    Токен ОК — короткоживущий: обменивается на долгоживущий отдельно (app_id +
    application_secret_key). Здесь используется готовый access_token, который положили в настройки.
    """
    token = (conn.get("token") or "").strip()
    gid = str(conn.get("target") or "").strip()
    if not token or not gid:
        return {"ok": False, "detail": "не задан токен приложения ОК или id группы"}
    attachment = {"media": [{"type": "text", "text": text}]}
    if media_url:
        attachment["media"].append({"type": "photo", "url": media_url})
    params = {"application_key": (conn.get("app_key") or "").strip(),
              "gid": gid, "type": "GROUP_THEME", "attachment": json.dumps(attachment, ensure_ascii=False),
              "access_token": token, "format": "json"}
    try:
        with _http() as client:
            r = client.post("https://api.ok.ru/fb.do", data=params)
        j = r.json() if r.content else {}
        if j.get("error_code"):
            return {"ok": False, "detail": "Одноклассники ответили %s: %s" % (j.get("error_code"), str(j.get("error_msg"))[:160])}
        return {"ok": True, "detail": "опубликовано в группе"}
    except Exception as e:  # noqa: BLE001
        return _err("нет связи с Одноклассниками", str(e))


def send_instagram(conn: dict, text: str, media_url: str = "") -> dict:
    """Instagram через Graph API: сначала создаём контейнер, потом публикуем его.

    Instagram не принимает пост без картинки — это его правило, а не наше:
    поэтому отказ здесь честный и понятный («нужно фото»), а не «ошибка 400».
    """
    token = (conn.get("token") or "").strip()
    ig = str(conn.get("target") or "").strip()
    if not token or not ig:
        return {"ok": False, "detail": "не задан токен и id бизнес-аккаунта Instagram"}
    if not media_url:
        return {"ok": False, "detail": "Instagram не принимает пост без фото"}
    base = "https://graph.facebook.com/v21.0/%s" % ig
    try:
        with _http() as client:
            c = client.post(base + "/media", data={"image_url": media_url, "caption": text,
                                                   "access_token": token}).json()
            if c.get("error"):
                return {"ok": False, "detail": "Instagram ответил: " + str(c["error"].get("message"))[:160]}
            pub = client.post(base + "/media_publish", data={"creation_id": c.get("id"),
                                                             "access_token": token}).json()
            if pub.get("error"):
                return {"ok": False, "detail": "Instagram ответил: " + str(pub["error"].get("message"))[:160]}
        return {"ok": True, "detail": "опубликовано"}
    except Exception as e:  # noqa: BLE001
        return _err("нет связи с Instagram", str(e))


def send_webhook(conn: dict, text: str, media_url: str = "", extra: dict | None = None) -> dict:
    """Своя площадка: POST JSON на адрес из настроек.

    Что уходит: {title, text, platform, media_url, meta}. Формат простой нарочно —
    принять его может любой бот, Make/n8n, свой обработчик. Ответ считается успешным
    по коду 2xx; текст ответа сохраняем (первые 200 символов) — по нему видно, что дальше.
    """
    url = (conn.get("url") or "").strip()
    if not url:
        return {"ok": False, "detail": "не задан адрес своей площадки"}
    payload = {"title": (extra or {}).get("title", ""), "text": text,
               "platform": "webhook", "media_url": media_url, "meta": (extra or {})}
    try:
        with _http() as client:
            r = client.post(url, json=payload, headers={"Content-Type": "application/json"})
        body = (r.text or "").strip()[:200]
        if 200 <= r.status_code < 300:
            return {"ok": True, "detail": "ответ " + str(r.status_code) + (": " + body if body else "")}
        return {"ok": False, "detail": "адрес ответил кодом %s: %s" % (r.status_code, body)}
    except Exception as e:  # noqa: BLE001
        return _err("не удалось вызвать адрес", str(e))


#: Ключ площадки → функция отправки. Добавили площадку — добавили строку здесь.
SENDERS: dict[str, Callable[..., dict]] = {
    "telegram": send_telegram,
    "vk": send_vk,
    "ok": send_ok,
    "instagram": send_instagram,
    "webhook": send_webhook,
}


def sender(key: str):
    """Функция отправки для площадки. Нет площадки — None, а не исключение."""
    return SENDERS.get(str(key or "").strip().lower())


def send(key: str, conn: dict, text: str, media_url: str = "", extra: dict | None = None) -> dict:
    """Отправить в одну площадку. Единая точка входа — её зовёт очередь.

    Сети касается только эта функция. Все отказы возвращаются словами:
    вызывающий код пишет их в результат и показывает человеку.
    """
    fn = sender(key)
    if not fn:
        return {"ok": False, "detail": "площадка «%s» пока не поддерживает отправку" % key}
    if key == "webhook":
        return fn(conn, text, media_url, extra=extra)
    return fn(conn, text, media_url)


def debug_enabled() -> bool:
    """Подробный разбор вызовов в лог — только при включённом флаге окружения.

    По умолчанию выключено: httpx печатает полный URL запроса, а в URL Telegram лежит
    токен бота. Уже был случай, когда токен попал в docker logs (задача #304).
    """
    return os.getenv("CF_DEBUG_HTTP", "") == "1"
