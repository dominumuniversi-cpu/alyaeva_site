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
ADMIN = "/" + config.ADMIN_SLUG if config.ADMIN_SLUG else "/admin"


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
