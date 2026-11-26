"""Страница контент-завода: /admin/posts и его приёмы.

Модуль сам себе страница: маршруты, формат ответов и логика живут здесь, а оболочка сайта
(меню админки, стили, CSRF) приходит хуками hooks.admin и hooks.render. Поэтому перенос
на другой сайт — это подмена двух функций, а не переписывание страницы.

Зачем на странице всё сразу (материал, площадки, расписание, очередь) — потому что это
один пост. Пять экранов для одного поста заставляют человека прыгать между вкладками.
"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from . import access, adapt, generator, hooks, llm, platforms, queue, settings

router = APIRouter()

TARGET_KEYS = [p.key for p in platforms.PLATFORMS]


def _split(raw: str) -> list[str]:
    """Строка 'telegram, vk,' → площадки, которые действительно существуют."""
    got = [x.strip() for x in str(raw or "").replace(";", ",").split(",")]
    return [x for x in got if platforms.get(x)]


def _links(raw: str) -> list[str]:
    """Ссылки из поля «ссылки»: по одной в строке. Не URL отбрасываем — это опечатка."""
    out = []
    for line in str(raw or "").splitlines():
        line = line.strip()
        if line.startswith("http://") or line.startswith("https://"):
            out.append(line)
    return out


def _handles() -> dict:
    """@аккаунт каждой площадки из настроек целей — для подписей в конце поста."""
    return {k: str(settings.get("cf.targets." + k + ".handle", "") or "") for k in TARGET_KEYS}


def _previews(body: str, chans: list[str], links: list[str]) -> dict:
    """Текст, который уйдёт в каждую площадку. Считается при сохранении, а не при публикации.

    Почему при сохранении: человек должен видеть адаптацию, пока пишет. Считать только
    в момент отправки — значит показать ему одно, а отправить другое.
    """
    return adapt.adapt_all(body, chans or TARGET_KEYS, links, _handles())


def _variant_rows(previews: dict) -> dict:
    """Готовые строки для cf_variants: текст + пометка «правил человек»."""
    return {k: {"text": v["text"], "edited_by_human": False} for k, v in previews.items()}


def _as_time(value: str):
    """'2026-11-27T09:00' из datetime-local → время для базы. Пусто → None."""
    value = str(value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _targets_table() -> list[dict]:
    """Площадки для страницы: подключена ли, что настроено, что мешает."""
    out = []
    for p in platforms.PLATFORMS:
        conn = queue.target_conn(p.key)
        out.append({
            "key": p.key, "name": p.name, "short": p.short or p.name[:2].upper(),
            "limit": p.limit, "needs_media": p.needs_media, "needs": p.needs,
            "ready": queue.target_enabled(p.key),
            "address": str(conn.get("target") or ""),
            "has_token": bool((conn.get("token") or "").strip()),
        })
    return out


def _page(request: Request, user, saved: str = "", bad: bool = False):
    """Общая отрисовка страницы завода."""
    rows = access.list_posts()
    conn = llm.active()
    for r in rows:
        # «Можно ли опубликовать» — по собственным неотправленным площадкам поста,
        # а не «есть ли вообще хоть одна настроенная площадка где-то».
        pend = [k for k in (r.get("channels_list") or []) if r.get("status") != "sent"]
        r["can_publish"] = any(queue.target_enabled(k) for k in pend)
    return hooks.render(
        request, "cf_admin_posts.html",
        user=user, posts=rows, targets=_targets_table(), platforms=platforms.PLATFORMS,
        llm_on=bool(conn), llm_label=(conn["label"] if conn else ""),
        llm_rows=llm.rows(), history=generator.history(5), ready=queue.ready_list(),
        default_when=datetime.now().strftime("%Y-%m-%dT09:00"),
        saved=saved, bad=bad, statuses=access.STATUSES,
    )


@router.get("/admin/posts")
def admin_posts(request: Request):
    """Контент-завод: лента постов, материал, площадки, расписание, настройки целей."""
    user, deny = hooks.admin(request)
    if deny:
        return deny
    return _page(request, user, request.query_params.get("saved", ""),
                 request.query_params.get("bad", "") == "1")
@router.post("/api/admin/cf/preview")
def cf_preview(request: Request, body: str = Form(""), channels: str = Form(""),
               links: str = Form(""), media: str = Form("")):
    """Пересчитать тексты под площадки, пока человек печатает.

    Страница зовёт это при паузе в наборе: человек видит, как пост «примеривается»
    к Telegram и к ВК, ничего не сохраняя.
    """
    user, deny = hooks.admin(request)
    if deny:
        return JSONResponse({"ok": False, "error": "Нужно войти"}, status_code=401)
    chans = _split(channels) or TARGET_KEYS
    got = _previews(body, chans, _links(links))
    for k, v in got.items():
        v["needs_media_now"] = v["needs_media"] and not (media or "").strip()
        v["ready"] = queue.target_enabled(k)
    return JSONResponse({"ok": True, "variants": got})


@router.post("/api/admin/cf/post")
def cf_create(request: Request, body: str = Form(""), title: str = Form(""),
              channels: str = Form(""), media: str = Form(""), links: str = Form(""),
              publish_at: str = Form("")):
    """Сохранить пост: материал + адаптированные тексты по каждой площадке.

    Пустой текст и пустые площадки отбиваются сразу и словами: пост без площадок
    некуда отправлять, а пост без текста площадка примет за ошибку.
    """
    user, deny = hooks.admin(request)
    if deny:
        return JSONResponse({"ok": False, "error": "Нужно войти"}, status_code=401)
    text = (body or "").strip()
    chans = _split(channels)
    if not text:
        return JSONResponse({"ok": False, "error": "Пустой пост публиковать некуда"}, status_code=400)
    if not chans:
        return JSONResponse({"ok": False, "error": "Отметьте хотя бы одну площадку"}, status_code=400)
    previews = _previews(text, chans, _links(links))
    when = _as_time(publish_at)
    post_id = access.create(text, title, chans, (media or "").strip(),
                            (user or {}).get("id"), _variant_rows(previews), when)
    if when:
        queue.schedule(post_id, when)
    return JSONResponse({
        "ok": True, "id": post_id, "variants": previews,
        "channels_text": ", ".join(platforms.get(k).name for k in chans),
        "status": "queued" if when else "draft",
        "status_name": access.STATUSES["queued" if when else "draft"],
    })


@router.post("/api/admin/cf/post/{post_id}/variants")
def cf_save_variants(request: Request, post_id: int, texts: str = Form("{}")):
    """Сохранить правки текстов по площадкам (JSON: {"telegram": "текст", ...}).

    Правка человека помечается — и адаптер больше не перезаписывает её при следующем
    сохранении материала. Иначе «поправила под ВК» терялось бы при каждом сохранении.
    """
    user, deny = hooks.admin(request)
    if deny:
        return JSONResponse({"ok": False, "error": "Нужно войти"}, status_code=401)
    post = access.get(post_id)
    if not post:
        return JSONResponse({"ok": False, "error": "Пост не найден"}, status_code=404)
    try:
        data = json.loads(texts or "{}")
    except ValueError:
        return JSONResponse({"ok": False, "error": "Не удалось разобрать текст"}, status_code=400)
    rows = {}
    for k, v in (data or {}).items():
        if platforms.get(k):
            rows[k] = {"text": str(v or ""), "edited_by_human": True}
    access.save_variants(post_id, rows)
    return JSONResponse({"ok": True, "saved": len(rows)})


@router.post("/api/admin/cf/post/{post_id}/save")
def cf_update(request: Request, post_id: int, body: str = Form(""), title: str = Form(""),
              channels: str = Form(""), media: str | None = Form(None), links: str = Form(""),
              publish_at: str | None = Form(None)):
    """Правка поста. Пустое поле НЕ стирает материал: «пусто» значит «не меняли».

    Ровно на этом когда-то сломалась форма настроек сайта: пустое поле стирало настройку,
    и отправка переставала работать «сама по себе». Здесь тот же порядок.
    """
    user, deny = hooks.admin(request)
    if deny:
        return JSONResponse({"ok": False, "error": "Нужно войти"}, status_code=401)
    post = access.get(post_id)
    if not post:
        return JSONResponse({"ok": False, "error": "Пост не найден"}, status_code=404)
    if post["status"] in ("sent", "sending"):
        return JSONResponse({"ok": False, "error": queue.protect_text(post), "copy": True},
                            status_code=400)
    chans = _split(channels)
    new_body = (body or "").strip()
    access.update(post_id, body=new_body or None, title=(title or "").strip() or None,
                  channels=chans or None, media=None if media is None else (media or "").strip())
    variants = {}
    if new_body or chans or links:
        src = new_body or post["body"]
        ch = chans or post["channels_list"]
        # Уже отредактированные человеком площадки не перезаписываем: правка важнее шаблона.
        edited = {k for k, v in (post.get("variants") or {}).items() if v.get("edited_by_human")}
        previews = _previews(src, ch, _links(links))
        variants = {k: v for k, v in previews.items() if k not in edited}
        access.save_variants(post_id, _variant_rows(variants))
    if publish_at is not None:
        queue.schedule(post_id, _as_time(publish_at))
    return JSONResponse({"ok": True, "variants": variants})


@router.post("/api/admin/cf/post/{post_id}/delete")
def cf_delete(request: Request, post_id: int):
    """Удалить черновик. Опубликованный не удаляем — это история того, что видели люди."""
    user, deny = hooks.admin(request)
    if deny:
        return JSONResponse({"ok": False, "error": "Нужно войти"}, status_code=401)
    if not access.delete(post_id):
        return JSONResponse({"ok": False, "error": "Удалить можно только черновик"}, status_code=400)
    return JSONResponse({"ok": True})


@router.post("/api/admin/cf/post/{post_id}/publish")
def cf_publish(request: Request, post_id: int):
    """Опубликовать сейчас. Итог приходит словами + ответ каждой площадки."""
    user, deny = hooks.admin(request)
    if deny:
        return JSONResponse({"ok": False, "error": "Нужно войти"}, status_code=401)
    got = queue.publish(post_id)
    if got.get("error") and not got.get("status"):
        return JSONResponse({"ok": False, "error": got["error"]}, status_code=404)
    return JSONResponse(got)


@router.post("/api/admin/cf/post/{post_id}/schedule")
def cf_schedule(request: Request, post_id: int, when: str = Form("")):
    """Поставить в очередь или снять с очереди. Пустое время — снять (останется черновиком)."""
    user, deny = hooks.admin(request)
    if deny:
        return JSONResponse({"ok": False, "error": "Нужно войти"}, status_code=401)
    return JSONResponse(queue.schedule(post_id, _as_time(when)))


@router.post("/api/admin/cf/post/{post_id}/copy")
def cf_copy(request: Request, post_id: int):
    """Копия опубликованного в правку: новый черновик с тем же материалом и текстами."""
    user, deny = hooks.admin(request)
    if deny:
        return JSONResponse({"ok": False, "error": "Нужно войти"}, status_code=401)
    post = access.get(post_id)
    if not post:
        return JSONResponse({"ok": False, "error": "Пост не найден"}, status_code=404)
    new_id = access.create(post["body"], post.get("title") or "", post["channels_list"],
                           post.get("media_url") or "", (user or {}).get("id"),
                           {k: {"text": v.get("text", ""), "edited_by_human": True}
                            for k, v in (post.get("variants") or {}).items()})
    return JSONResponse({"ok": True, "id": new_id})


@router.post("/api/admin/cf/generate")
def cf_generate(request: Request, topic: str = Form(""), kind: str = Form(""),
                extra: str = Form(""), post_id: str = Form("")):
    """Сочинить пост нейросетью. Не подключено — отказ словами и просьбой подключить.

    Это и есть «серая кнопка»: страница её гасит, а сервер всё равно проверяет сам —
    потому что кнопку можно разблокировать в консоли браузера, а ключа у сервера нет.
    """
    user, deny = hooks.admin(request)
    if deny:
        return JSONResponse({"ok": False, "error": "Нужно войти"}, status_code=401)
    if not (topic or "").strip():
        return JSONResponse({"ok": False, "error": "Опишите, о чём пост"}, status_code=400)
    got = generator.generate(topic, kind, extra, (user or {}).get("id"),
                             int(post_id) if str(post_id).strip().isdigit() else None)
    if not got.get("ok"):
        return JSONResponse({"ok": False, "error": got.get("error") or "Не получилось"}, status_code=400)
    return JSONResponse({k: v for k, v in got.items() if k != "error"})

@router.post("/admin/posts/targets")
def cf_targets_save(request: Request, telegram_address: str = Form(""), telegram_token: str = Form(""),
                    telegram_handle: str = Form(""), vk_address: str = Form(""), vk_token: str = Form(""),
                    vk_handle: str = Form(""), ok_address: str = Form(""), ok_token: str = Form(""),
                    ok_appkey: str = Form(""), ok_handle: str = Form(""), ig_address: str = Form(""),
                    ig_token: str = Form(""), ig_handle: str = Form(""), hook_url: str = Form("")):
    """Сохранить подключение площадок. Пустое поле не стирает прежнее значение.

    Ответ без перезагрузки — как у «Интеграции»: страница показывает плашку «сохранено»
    и обновляет значки площадок.
    """
    user, deny = hooks.admin(request)
    if deny:
        return JSONResponse({"ok": False, "error": "Нужно войти"}, status_code=401)
    pairs = [
        ("telegram.address", telegram_address), ("telegram.token", telegram_token),
        ("telegram.handle", telegram_handle),
        ("vk.address", vk_address), ("vk.token", vk_token), ("vk.handle", vk_handle),
        ("ok.address", ok_address), ("ok.token", ok_token), ("ok.app_key", ok_appkey),
        ("ok.handle", ok_handle),
        ("instagram.address", ig_address), ("instagram.token", ig_token),
        ("instagram.handle", ig_handle),
        ("webhook.address", hook_url),
    ]
    changed = []
    for short, value in pairs:
        value = (value or "").strip()
        if not value:
            continue  # пустое не трогаем: одно потерянное поле не должно гасить площадку
        key = "cf.targets." + short
        if str(settings.get(key, "") or "") == value:
            continue
        settings.set(key, value, (user or {}).get("id"))
        changed.append(short)
    if "application/json" in (request.headers.get("accept") or ""):
        return JSONResponse({"ok": True, "changed": changed, "ready": queue.ready_list()})
    return RedirectResponse("/admin/posts?saved=" + str(len(changed)), status_code=303)


@router.post("/admin/posts/llm")
def cf_llm_save(request: Request, provider: str = Form(""), token: str = Form(""),
                model: str = Form(""), base_url: str = Form(""), enabled: str = Form(""),
                proxy: str = Form("")):
    """Подключить нейросеть. Ключ хранится шифром, наружу не отдаётся никогда."""
    user, deny = hooks.admin(request)
    if deny:
        return JSONResponse({"ok": False, "error": "Нужно войти"}, status_code=401)
    if provider not in llm.PROVIDERS:
        return JSONResponse({"ok": False, "error": "Неизвестный сервис"}, status_code=400)
    on = str(enabled).strip() in ("1", "on", "true", "yes")
    llm.save(provider, token or None, model, base_url, on)
    if (proxy or "").strip():
        settings.set("cf.llm.proxy", proxy.strip(), (user or {}).get("id"))
    active = llm.active()
    return JSONResponse({"ok": True, "enabled": bool(active),
                         "label": active["label"] if active else "",
                         "note": "" if active else
                         "Сохранено. Кнопка «Сочинить» станет живой, когда будет включён сервис с ключом."})


@router.post("/admin/posts/llm/{provider}/off")
def cf_llm_off(request: Request, provider: str):
    """Отключить нейросеть и убрать ключ. Кнопка «Сочинить» снова станет серой."""
    user, deny = hooks.admin(request)
    if deny:
        return JSONResponse({"ok": False, "error": "Нужно войти"}, status_code=401)
    if provider not in llm.PROVIDERS:
        return JSONResponse({"ok": False, "error": "Неизвестный сервис"}, status_code=400)
    llm.clear_key(provider)
    return JSONResponse({"ok": True, "enabled": llm.enabled()})

@router.get("/api/admin/cf/post/{post_id}/variants.json")
def cf_get_variants(request: Request, post_id: int):
    """Тексты поста по площадкам — для раскрытия «Тексты по площадкам» в ленте.

    Если вариантов у старого поста нет (он сохранён до завода), считаем их на лету
    из материала: человек видит адаптацию, даже если пост появился раньше.
    """
    user, deny = hooks.admin(request)
    if deny:
        return JSONResponse({"ok": False, "error": "Нужно войти"}, status_code=401)
    post = access.get(post_id)
    if not post:
        return JSONResponse({"ok": False, "error": "Пост не найден"}, status_code=404)
    have = post.get("variants") or {}
    out = {}
    for k in post.get("channels_list") or []:
        p = platforms.get(k)
        if not p:
            continue
        v = have.get(k)
        if v and (v.get("text") or "").strip():
            out[k] = {"name": p.name, "text": v["text"], "limit": p.limit,
                      "edited_by_human": bool(v.get("edited_by_human"))}
        else:
            got = adapt.adapt(p, post["body"], _links(settings.get("cf.links", "") or ""),
                              _handles().get(k, ""))
            out[k] = {"name": p.name, "text": got["text"], "limit": p.limit, "edited_by_human": False}
    return JSONResponse({"ok": True, "variants": out})
