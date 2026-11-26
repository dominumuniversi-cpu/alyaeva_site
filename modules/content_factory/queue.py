"""Очередь и расписание: что уходит, когда и по какой площадке.

Завод без расписания — это редактор с кнопкой «опубликовать сейчас». Владельцу нужно
«поставил на 9:00 — ушло в 9:00», иначе про расписание её нет смысла заполнять.

Кто отправляет: фоновый поток (worker.py) раз в минуту берёт посты, которым пора.
Граница с сетью: пост забирается из базы, база отпускается, и только потом идёт вызов
площадки. Нельзя держать соединение из пула, пока ждём чужой сервис.

`publish_now` работает и без фонового потока — им пользуется кнопка «Опубликовать».
"""
from __future__ import annotations

import hashlib
import json

from . import access, channels, dbsession, platforms, settings


def fingerprint(platform: str, text: str) -> str:
    """Отпечаток: площадка + текст. По нему видно «этот текст в эту площадку уже уходил»."""
    raw = (platform + "\x00" + (text or "")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def already_sent(platform: str, text: str) -> bool:
    """Публиковался ли этот текст на этой площадке. Защита от двойной публикации."""
    row = dbsession.one("SELECT 1 AS x FROM cf_fingerprints WHERE hash = %s", (fingerprint(platform, text),))
    return bool(row)


def remember(platform: str, text: str, post_id: int) -> None:
    """Запомнить, что текст ушёл. Повторное нажатие «Опубликовать» второй раз не отправит."""
    dbsession.tx(
        "INSERT INTO cf_fingerprints (hash, platform, post_id) VALUES (%s, %s, %s) ON CONFLICT (hash) DO NOTHING",
        (fingerprint(platform, text), platform, post_id),
    )


def target_conn(platform: str) -> dict:
    """Соединение площадки из настроек сайта: токен, адрес, что ещё нужно её API.

    Одна функция на все площадки: страница «Цели» и отправка читают одно и то же,
    поэтому «подключено» на странице и «отправлено» в жизни не разъедутся.
    """
    pre = "cf.targets." + platform + "."
    return {
        "token": str(settings.get(pre + "token", "") or ""),
        "target": str(settings.get(pre + "address", "") or ""),
        "url": str(settings.get(pre + "address", "") or ""),
        "app_key": str(settings.get(pre + "app_key", "") or ""),
        "proxy": str(settings.get("telegram.proxy", "") or ""),
    }


def target_enabled(platform: str) -> bool:
    """Можно ли сейчас отправить в площадку — по её собственным требованиям, а не «вообще»."""
    p = platforms.get(platform)
    if not p:
        return False
    conn = target_conn(platform)
    if platform == "webhook":
        return bool(conn["url"].strip())
    return bool(conn["token"].strip()) and bool(conn["target"].strip())


def ready_list() -> list[str]:
    """Какие площадки настроены прямо сейчас — для галочек на странице поста."""
    return [k for k in platforms.keys() if target_enabled(k)]


def next_variants(post: dict) -> dict:
    """Тексты, которые реально уйдут: правки человека важнее того, что сочинил адаптер.

    Почему так: человек может поправить пост под ВК («там длиннее и без тегов») — и правка
    обязана победить шаблон. Иначе кнопка «Опубликовать» отправляла бы не то, что видно на экране.
    """
    return {k: v.get("text", "") for k, v in (post.get("variants") or {}).items()}


def publish(post_id: int) -> dict:
    """Отправить пост во все его площадки. Уже занятый или отправленный пост не трогаем."""
    post = access.get(post_id)
    if not post:
        return {"ok": False, "error": "Пост не найден", "status": ""}
    if post["status"] in ("sent", "sending"):
        return {"ok": False, "error": protect_text(post), "status": post["status"]}
    if not access.claim(post_id):
        return {"ok": False, "error": "Пост уже отправляется другим действием", "status": ""}
    return _send_claimed(post)


def _send_claimed(post: dict) -> dict:
    """Отправка уже занятого поста. Возвращает итог словами + ответ каждой площадки."""
    variants = next_variants(post)
    media = post.get("media_url") or ""
    result: dict[str, str] = {}
    skipped: dict[str, str] = {}
    for key in post.get("channels_list") or []:
        if not target_enabled(key):
            skipped[key] = "площадка не подключена"
            continue
        text = (variants.get(key) or "").strip()
        if not text:
            skipped[key] = "нет текста для площадки"
            continue
        if already_sent(key, text):
            skipped[key] = "этот текст уже публиковался здесь"
            continue
        got = channels.send(key, target_conn(key), text, media,
                            extra={"title": post.get("title") or "", "post_id": post.get("id")})
        result[key] = ("ok: " + got.get("detail", "")) if got.get("ok") else ("fail: " + got.get("detail", ""))
        if got.get("ok"):
            remember(key, text, post.get("id"))
    sent = [k for k, v in result.items() if v.startswith("ok")]
    tried = len(result)
    if tried and len(sent) == tried:
        status = "sent"
    elif sent:
        status = "partial"
    else:
        status = "failed"
    if not tried:
        status = "failed"
    merged = dict(result)
    for k, v in skipped.items():
        merged[k] = "skip: " + v
    access.mark_result(post["id"], status, merged)
    return {
        "ok": bool(sent),
        "status": status,
        "status_name": access.STATUSES.get(status, status),
        "summary": summarize(status, sent, merged),
        "result": merged,
        "sent": sent,
        "error": "" if sent else first_reason(merged),
    }


def first_reason(merged: dict) -> str:
    """Первая человеческая причина, почему ничего не ушло. Пусто — причин нет."""
    for v in merged.values():
        if v.startswith("fail: "):
            return v[6:]
        if v.startswith("skip: "):
            return v[6:]
    return ""


def summarize(status: str, sent: list[str], merged: dict) -> str:
    """Итог словами, а не «ушёл не во все каналы».

    Уже было: на любой неуспех писало «Ушёл не во все каналы», и человек думал, что куда-то
    всё-таки ушло. Теперь итог говорит, что именно произошло.
    """
    names = {p.key: p.name for p in platforms.PLATFORMS}
    if status == "sent":
        return "Опубликовано: " + ", ".join(names.get(k, k) for k in sent)
    if status == "partial":
        return "Ушло только в: " + ", ".join(names.get(k, k) for k in sent)
    return "Ничего не ушло" + (": " + first_reason(merged) if first_reason(merged) else "")


def protect_text(post: dict) -> str:
    """Что сказать, если пост уже отправлен или отправляется."""
    if post["status"] == "sent":
        return "Этот пост уже опубликован — правка создаст копию"
    return "Пост уже отправляется"


def send_due(limit: int = 10) -> list[dict]:
    """Отправить всё, чему пора. Возвращает итоги — их пишет в лог фоновый поток."""
    out = []
    for post in access.due(limit):
        if not access.claim(post["id"]):
            continue
        got = _send_claimed(post)
        out.append({"id": post["id"], "status": got["status"], "summary": got["summary"]})
    return out


def schedule(post_id: int, when) -> dict:
    """Поставить пост в очередь. Время пустое — снять с очереди (останется черновиком)."""
    if when:
        access.update(post_id, publish_at=when, status="queued")
        # Время возвращаем строкой: страница пишет человеку «уйдёт 27.11 в 09:00», не разбирая объект.
        return {"ok": True, "status": "queued", "status_name": access.STATUSES["queued"],
                "when": when.strftime("%Y-%m-%dT%H:%M")}
    access.update(post_id, publish_at=None, status="draft")
    return {"ok": True, "status": "draft", "status_name": access.STATUSES["draft"], "when": ""}
