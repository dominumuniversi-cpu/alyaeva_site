"""Общие хелперы для маршрутов: сессии, права, рендер шаблонов.

Вынесено из main, чтобы не было циклического импорта между main и роутерами.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import db
from config import config
from order_status import next_step, NEW_STATUS

templates = Jinja2Templates(directory="templates")
# Админка живёт по /admin — нормальный, читаемый адрес.
# Раньше был секретный слаг /6b03c5256535e9ab5a: владелец набирал его руками и путался.
ADMIN = "/admin"


def current_user(request: Request) -> dict | None:
    """Пользователь из cookie-сессии."""
    return db.session_user(request.cookies.get("sid", ""))


def require_user(request: Request):
    """Обязательная авторизация; вернуть пользователя или редирект."""
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return user


def require_admin(request: Request) -> dict | None:
    """Только администратор."""
    user = current_user(request)
    if not user or user["role"] != "admin":
        return None
    return user


def site_links() -> dict:
    """Публичные контакты и ссылки на мессенджеры — из настроек сайта.

    Одна правда: ссылки правятся в /admin/settings, а не хардкодятся в шаблонах.
    Раньше на главной они были вписаны руками, и изменить их можно было только в коде.
    """
    tg = str(db.get_setting("site.telegram", "") or "").strip()
    vk = str(db.get_setting("site.vk", "") or "").strip()
    wa = str(db.get_setting("site.whatsapp", "") or "").strip()
    inst = str(db.get_setting("site.instagram", "") or "").strip()
    return {
        "tg_url": tg, "vk_url": vk, "wa_url": wa, "inst_url": inst,
        "site_phone": str(db.get_setting("site.phone", "") or ""),
        "site_email": str(db.get_setting("site.email", "") or ""),
        "site_address": str(db.get_setting("site.address", "") or ""),
        "site_hours": str(db.get_setting("site.work_hours", "") or ""),
        "site_footer": str(db.get_setting("site.footer", "") or ""),
    }


def admin_counts() -> dict:
    """Сколько ждёт внимания администратора.

    Считается один раз на страницу. Нужно, чтобы Алена, войдя в админку,
    сразу видела, есть ли новые заказы — без обхода всех разделов.
    """
    try:
        with db.pool.connection() as conn:
            # Статус «только что пришёл» — из словаря, а не литералом:
            # иначе переименование статуса тихо оставит счётчик на нуле.
            row = conn.execute(
                "SELECT count(*) AS new_orders FROM orders WHERE status = %s", (NEW_STATUS,)
            ).fetchone()
        return {"new_orders": row["new_orders"]}
    except Exception:
        # Счётчик — украшение, а не смысл страницы: если запрос не удался, страница всё равно рисуется.
        return {"new_orders": 0}


def csrf_guard(request: Request) -> str:
    """Нужно ли этому браузеру подтверждать источник формы (см. main.deny_csrf).

    Пусто — ничего не добавляется в форму, всё как раньше. Возвращается "да",
    если у человека уже есть cookie-сессия, но cookie-признака проверки ещё нет.
    Такая страница обязана подписать формы, иначе отправка получит 403.
    """
    if not request.cookies.get("sid"):
        return ""
    return "" if request.cookies.get("csrf_ok") == "1" else "да"


def render(request: Request, name: str, **kw) -> HTMLResponse:
    """Рендер шаблона с общим контекстом.

    Контакты и ссылки на мессенджеры идут в каждый шаблон: шапка и подвал
    есть на всех страницах, дублировать их в каждом маршруте — лишнее.
    """
    user = kw.pop("user", None) or current_user(request)
    links = kw.pop("links", None) or site_links()
    counts = kw.pop("counts", None)
    if counts is None:
        counts = admin_counts() if (user and user.get("role") == "admin") else {}
    # Просмотр считаем здесь, а не в каждом маршруте: одна точка — один счётчик.
    # Тяжёлые страницы (админка, кабинет) в общую статистику не идут: там сидят свои.
    # Свои проверки не считаем: заголовок X-DSH-Check ставит агент, когда обходит сайт.
    # Без этого мои обходы попадали в статистику как «люди» (24.11.2026: 18 таких записей).
    if request.headers.get("x-dsh-check") != "1":
        try:
            import analytics
            analytics.track("view", request.url.path,
                            analytics.source_from_referer(request.headers.get("referer", "")))
        except Exception:
            pass
    # «Что дальше» по заказу — тем же словарём, что и статусы. Иначе в шаблоне
    # снова появится список из пяти старых статусов (так уже было).
    kw.setdefault("next_step", next_step)
    # Название статуса — из общего словаря (app/order_status.py), а не из шаблона.
    # В cabinet.html звалось status_name(...), а в контексте его не было: кабинет
    # падал бы 500 у любого, у кого есть заказ. Вход в кабинет без заказов этого не показывает (24.11.2026).
    if "status_name" not in kw:
        from order_status import status_name as _status_name  # импорт внутри: не тянуть приложение при импорте модуля
        kw["status_name"] = _status_name
    # Счётчик непрочитанных нужен шапке на каждой странице кабинета.
    unread = kw.pop("unread", 0)
    if user and not unread:
        try:
            import notifications
            with db.pool.connection() as conn:
                unread = notifications.unread_count(conn, user["id"])
        except Exception:
            unread = 0
    import datetime
    return templates.TemplateResponse(request, name,
                                      {"user": user, "admin_url": ADMIN, "links": links,
                                       "counts": counts, "unread": unread,
                                       "csrf_guard": csrf_guard(request),
                                       "year": datetime.date.today().year, **kw})


def is_browser(request) -> bool:
    """Пришёл человек из браузера или скрипт?

    Браузер просит text/html. fetch и curl — */* или application/json.
    """
    accept = (request.headers.get("accept") or "").lower()
    return "text/html" in accept


def denied(request, message: str = "Нет доступа", status: int = 403):
    """Отказ в доступе. Человеку — страница, скрипту — JSON.

    Раньше отказы всегда уходили JSON'ом, и владелец, открывший адрес
    в браузере, видел {"ok":false,"error":"Нет доступа"}.
    """
    from fastapi.responses import JSONResponse
    if is_browser(request):
        user = current_user(request)
        resp = render(request, "403.html", user=user,
                      who=(user.get("email") if user else None))
        resp.status_code = status
        return resp
    return JSONResponse({"ok": False, "error": message}, status_code=status)
