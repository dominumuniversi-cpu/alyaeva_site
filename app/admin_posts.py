"""Админка постов: редактор, черновики, история и публикация (задача #5).

Что делает эта страница и чего не делает:

- собирает пост и хранит его в таблице posts (было и раньше — не хватало экрана);
- публикует, если в настройках есть токены, и честно говорит, если их нет;
- не трогает расписание: отложенной публикации пока нет, для неё нужен фоновый планировщик
  (см. задачу #5, часть «отложенная публикация»).

Пустую строку в каналы не отправляем: пост без текста в Telegram — это ошибка, а не «почти сработало».
"""
from __future__ import annotations

import html
import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

import db
import messaging
import post_queue
import posts

router = APIRouter()


def _admin(request: Request):
    """Кто смотрит. Возвращает (пользователь, ответ-отказ) — так же, как в остальной админке.

    Различаем два случая, как везде: гость уходит на вход, вошедший без прав получает
    403-страницу. Молчаливый редирект на главную владелец уже ловил и просил так не делать.
    """
    from web_common import require_admin, denied

    user = require_admin(request)
    if not user:
        return None, denied(request)
    return user, None


def _ready_channels() -> list[str]:
    """Какие каналы настроены прямо сейчас. От этого зависит, включена ли кнопка публикации."""
    settings = {
        "telegram.token": db.get_setting("telegram.token", ""),
        "telegram.channel": db.get_setting("telegram.channel", ""),
        "vk.token": db.get_setting("vk.token", ""),
        "vk.group": db.get_setting("vk.group", ""),
    }
    return posts.channels_ready(settings)


@router.get("/admin/posts", response_class=HTMLResponse)
def admin_posts(request: Request):
    """Редактор поста и всё, что уже собрано."""
    from web_common import render

    user, deny = _admin(request)
    if deny:
        return deny
    ready = _ready_channels()
    rows = post_queue.list_posts()
    # Кнопка «Опубликовать» гасится не «вообще», а по конкретному посту:
    # пост только в VK — и кнопка ждёт настроенного VK, а не Telegram.
    for p in rows:
        p["can_publish"] = bool(set(p.get("channels_list") or []) & set(ready))
    return render(
        request,
        "admin_posts.html",
        user=user,
        posts=rows,
        ready=ready,
    )


@router.post("/api/admin/post")
def create_post(request: Request, body: str = Form(""), channels: str | None = Form(None),
                media_url: str = Form("")):
    """Сохранить черновик. Пустой текст отбиваем сразу: иначе он уйдёт в канал пустым."""
    user, deny = _admin(request)
    if deny:
        return JSONResponse({"ok": False, "error": "Нужно войти"}, status_code=401)
    text = (body or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "Пустой пост отправлять некуда"}, status_code=400)
    # Ловушка FastAPI (проверено 24.11.2026): при поле с значением по умолчанию
    # `channels: str = Form("telegram")` пустая строка `channels=` превращается в "telegram",
    # и пост молча уходил в Telegram, хотя канал не выбран. Поэтому умолчания нет (None),
    # а выбор делает человек. Так же ведёт себя и `create`: без каналов — ошибка, не подстановка.
    chans = [c.strip() for c in (channels or "").split(",") if c.strip() in post_queue.CHANNELS]
    if not chans:
        return JSONResponse({"ok": False, "error": "Выберите хотя бы один канал"}, status_code=400)
    post_id = post_queue.create(text, chans, (media_url or "").strip(), user.get("id"))
    names = [{"telegram": "Telegram", "vk": "ВКонтакте"}[c] for c in post_queue.CHANNELS if c in chans]
    return JSONResponse({
        "ok": True,
        "id": post_id,
        "escaped": html.escape(text),
        "channels_text": ", ".join(names),
        "status_name": post_queue.STATUSES["draft"],
    })


@router.post("/api/admin/post/{post_id}/save")
def save_post(request: Request, post_id: int, body: str = Form("")):
    """Правка текста черновика. Публикованный пост не меняем: это история того, что люди видели."""
    user, deny = _admin(request)
    if deny:
        return JSONResponse({"ok": False, "error": "Нужно войти"}, status_code=401)
    row = post_queue.get_post(post_id)
    if not row:
        return JSONResponse({"ok": False, "error": "Пост не найден"}, status_code=404)
    if row["status"] == "sent":
        return JSONResponse({"ok": False, "error": "Опубликованный пост не правится"}, status_code=400)
    text = (body or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "Текст не может быть пустым"}, status_code=400)
    post_queue.save(post_id, text, row["channels_list"], row["media_url"] or "", row["status"])
    return JSONResponse({"ok": True})


@router.post("/api/admin/post/{post_id}/delete")
def delete_post(request: Request, post_id: int):
    """Удалить черновик. История отправленного остаётся."""
    user, deny = _admin(request)
    if deny:
        return JSONResponse({"ok": False, "error": "Нужно войти"}, status_code=401)
    if not post_queue.delete(post_id):
        return JSONResponse({"ok": False, "error": "Нельзя удалить этот пост"}, status_code=400)
    return JSONResponse({"ok": True})


@router.post("/api/admin/post/{post_id}/publish")
def publish_post(request: Request, post_id: int):
    """Отправить в каналы. Без токенов честно отказываем, а не делаем вид, что ушло."""
    user, deny = _admin(request)
    if deny:
        return JSONResponse({"ok": False, "error": "Нужно войти"}, status_code=401)
    row = post_queue.get_post(post_id)
    if not row:
        return JSONResponse({"ok": False, "error": "Пост не найден"}, status_code=404)
    if row["status"] == "sent":
        return JSONResponse({"ok": False, "error": "Уже опубликован"}, status_code=400)

    ready = _ready_channels()
    chans = [c for c in row["channels_list"] if c in ready]
    if not chans:
        return JSONResponse({
            "ok": False,
            "error": "Каналы не настроены — впишите токены в «Приём сообщений»",
        }, status_code=400)

    text = row["body"]
    result: dict[str, str] = {}
    if "telegram" in chans:
        chat = str(db.get_setting("telegram.channel", "") or "").strip()
        result["telegram"] = "ok" if messaging.send_telegram(chat, posts.cut(text, posts.MAX_TELEGRAM)) else "fail"
    if "vk" in chans:
        group = str(db.get_setting("vk.group", "") or "").strip()
        # ВКонтакте разметку не понимает: отправляем чистый текст.
        result["vk"] = "ok" if messaging.send_vk(group, posts.html_to_plain(posts.cut(text, posts.MAX_VK))) else "fail"

    sent = [c for c, v in result.items() if v == "ok"]
    status = "sent" if len(sent) == len(chans) else ("partial" if sent else "failed")
    post_queue.mark_result(post_id, status, result)
    return JSONResponse({
        "ok": True,
        "status": status,
        "status_name": post_queue.STATUSES[status],
        "result": json.dumps(result, ensure_ascii=False),
    })
