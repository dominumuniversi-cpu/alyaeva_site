"""Общие хелперы для маршрутов: сессии, права, рендер шаблонов.

Вынесено из main, чтобы не было циклического импорта между main и роутерами.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import db
from config import config

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


def render(request: Request, name: str, **kw) -> HTMLResponse:
    """Рендер шаблона с общим контекстом."""
    user = kw.pop("user", None) or current_user(request)
    return templates.TemplateResponse(request, name, {"user": user, "admin_url": ADMIN, **kw})


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
