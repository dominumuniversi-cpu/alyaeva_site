"""Мостик сайта: как модуль зовёт то, что принадлежит сайту.

Единственное место, которое ЗНАЕТ про этот сайт. На другом сайте меняется только этот файл
(или заменяется адаптером): проверка администратора, шаблоны, адрес меню, стили.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import RedirectResponse

import db

#: Куда вести не-админа и куда возвращать после входа.
ADMIN_PREFIX = "/admin"


def admin(request: Request):
    """Админ по cookie-сессии сайта или (None, ответ-отказ).

    Различаем два случая, как везде на сайте: гость уходит на вход (и вернётся сюда),
    вошедший без прав получает 403-страницу. Молчаливый редирект сюда уже был и не понравился.
    """
    from web_common import current_user
    user = current_user(request)
    if user and user.get("role") == "admin":
        return user, None
    if not user:
        from urllib.parse import quote
        return None, RedirectResponse("/login?next=" + quote(request.url.path), status_code=303)
    from web_common import denied
    return None, denied(request)


def render(request: Request, name: str, **kw):
    """Отрисовать шаблон модуля в оболочке сайта (меню админки, стили, шапка и подвал).

    Шаблоны модуля лежат рядом с модулем, а base.html — у сайта. Поэтому загрузчик
    один список: сначала шаблоны модуля, потом сайта. Без этого `{% extends "base.html" %}`
    не найдёт базу и страница упадёт 500.
    """
    from web_common import render as site_render, templates
    from jinja2 import ChoiceLoader, FileSystemLoader

    env = templates.env
    if not getattr(env, "_cf_loader", False):
        env.loader = ChoiceLoader([FileSystemLoader("modules/content_factory/templates"), env.loader])
        env._cf_loader = True
    kw.setdefault("admin_url", ADMIN_PREFIX)
    return site_render(request, name, **kw)
