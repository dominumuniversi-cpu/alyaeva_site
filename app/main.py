"""alyaeva.pro — приложение: авторизация, личный кабинет, админка.

Запуск: uvicorn main:app --host 0.0.0.0 --port 8000
Статика сайта отдаётся nginx'ом напрямую, сюда идут только динамические пути.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import db
from config import config

from web_common import ADMIN, current_user, render, require_admin, require_user, templates  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Открыть/закрыть пул БД вместе с приложением."""
    db.open_pool()
    yield
    db.close_pool()


app = FastAPI(title="alyaeva.pro", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

import orders as orders_mod  # noqa: E402
from routes_shop import router as shop_router, status_name  # noqa: E402
app.include_router(shop_router)


@app.get("/health")
def health():
    """Проверка живости приложения (для nginx и мониторинга)."""
    try:
        with db.pool.connection() as conn:
            conn.execute("SELECT 1")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------- Вход / выход ----------------

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = "", next: str = "", reason: str = ""):
    """Форма входа. Хранит, куда человек шёл: после входа вернём его туда."""
    user = current_user(request)
    if user:
        target = _safe_next(next, ADMIN if user["role"] == "admin" else "/cabinet")
        return RedirectResponse(target, status_code=303)
    if reason == "order":
        # Человек нажал «Заказать» — объясняем, зачем вход, а не просто показываем форму.
        pass
    messages = {
        "reg": "Не получилось зарегистрироваться: проверьте почту и пароль (минимум 8 символов).",
        "exists": "Аккаунт с такой почтой уже есть — войдите.",
        "1": "Неверная почта или пароль.",
    }
    return render(request, "login.html", user=None, error=messages.get(error, ""),
                  next=_safe_next(next, ""), reason=reason)


def _safe_next(value: str, default: str) -> str:
    """Куда вернуть после входа. Только внутренние адреса — иначе открытый редирект."""
    value = (value or "").strip()
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return default
    return value


@app.get("/order", response_class=HTMLResponse)
def order_page(request: Request):
    """Страница заказа. Заказывать могут только вошедшие — так заказ сразу привязан к клиенту.

    Гость уходит на вход с пометкой, что шёл за заказом, и после входа попадает сюда же.
    """
    user = current_user(request)
    if not user:
        return RedirectResponse("/login?next=/order&reason=order", status_code=303)
    return templates.TemplateResponse(request, "order.html", {"user": user})


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...),
          next: str = Form("")):
    """Проверка логина и пароля, выдача cookie-сессии."""
    user = db.find_user_by_email(email.strip())
    if not user or not db.verify_password(password, user["password_hash"]):
        return render(request, "login.html", error="Неверный e-mail или пароль", user=None,
                      next=_safe_next(next, ""), reason="")
    token = db.create_session(
        user["id"],
        ua=request.headers.get("user-agent", ""),
        ip=request.client.host if request.client else "",
    )
    # Админ всё равно идёт в админку, но если он явно шёл заказывать — ведём к заказу.
    wanted = _safe_next(next, "")
    target = wanted or (ADMIN if user["role"] == "admin" else "/cabinet")
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie("sid", token, httponly=True, secure=True, samesite="lax", max_age=30 * 86400)
    return resp


@app.get("/logout")
@app.post("/logout")
def logout(request: Request):
    """Выход: удалить сессию и cookie."""
    token = request.cookies.get("sid", "")
    if token:
        db.drop_session(token)
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie("sid")
    return resp


# ---------------- Личный кабинет ----------------

@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, error: str = "", next: str = ""):
    """Форма регистрации."""
    if current_user(request):
        return RedirectResponse(_safe_next(next, "/cabinet"), status_code=303)
    return render(request, "register.html", user=None, error=error, next=_safe_next(next, ""))


@app.post("/register")
def register(request: Request, email: str = Form(""), password: str = Form(""),
             full_name: str = Form(""), phone: str = Form(""), next: str = Form("")):
    """Регистрация клиента. Гостевые заказы с тем же телефоном переходят к нему."""
    email = (email or "").strip().lower()
    if not email or len(password or "") < 8:
        return RedirectResponse("/login?error=reg", status_code=303)
    if db.find_user_by_email(email):
        return RedirectResponse("/login?error=exists", status_code=303)
    uid = db.create_user(email, password, full_name.strip(), "customer", phone.strip())
    # Заказы, оформленные гостем с этим телефоном, становились «ничьими» —
    # теперь у них есть владелец, и фото из guest/ попадают в его кабинет.
    if phone.strip():
        orders_mod.claim_guest_orders(uid, phone.strip(), email)
    token = db.create_session(uid, ua=request.headers.get("user-agent", ""),
                              ip=request.client.host if request.client else "")
    resp = RedirectResponse(_safe_next(next, "/cabinet"), status_code=303)
    resp.set_cookie("sid", token, httponly=True, secure=True, samesite="lax", max_age=30 * 86400)
    return resp


@app.get("/cabinet", response_class=HTMLResponse)
def cabinet(request: Request):
    """Заказы клиента."""
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    with db.pool.connection() as conn:
        orders = conn.execute(
            """SELECT id, number, status, total_rub, created_at, comment
               FROM orders WHERE user_id = %s ORDER BY created_at DESC""",
            (user["id"],),
        ).fetchall()
        # Фото раскладываем по заказам сразу — иначе в шаблоне будет запрос на каждый заказ.
        photos = conn.execute(
            """SELECT f.id, f.orig_name, p.order_id
               FROM project_files f JOIN projects p ON p.id = f.project_id
               WHERE p.order_id = ANY(%s) ORDER BY f.created_at""",
            ([o["id"] for o in orders] or [0],),
        ).fetchall()
    photos_by_order = {}
    for p in photos:
        photos_by_order.setdefault(p["order_id"], []).append(p)
    return render(request, "cabinet.html", user=user, orders=orders,
                  photos_by_order=photos_by_order, status_name=status_name)


@app.get("/cabinet/projects", response_class=HTMLResponse)
def projects_page(request: Request):
    """Проекты (папки) с фотографиями клиента."""
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    with db.pool.connection() as conn:
        projects = conn.execute(
            """SELECT p.*, (SELECT count(*) FROM project_files f WHERE f.project_id = p.id) AS files
               FROM projects p WHERE p.user_id = %s ORDER BY p.created_at DESC""",
            (user["id"],),
        ).fetchall()
    return render(request, "projects.html", user=user, projects=projects)


# ---------------- Админка ----------------

@app.get(ADMIN, response_class=HTMLResponse)
def admin_orders(request: Request):
    """Заказы: список для администратора."""
    user = require_admin(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db.pool.connection() as conn:
        orders = conn.execute(
            """SELECT o.*, u.email AS user_email,
                 (SELECT count(*) FROM projects p WHERE p.order_id = o.id) AS projects
               FROM orders o LEFT JOIN users u ON u.id = o.user_id
               ORDER BY o.created_at DESC LIMIT 200"""
        ).fetchall()
    return render(request, "admin_orders.html", user=user, orders=orders)


@app.get(ADMIN + "/inbox", response_class=HTMLResponse)
def admin_inbox(request: Request):
    """Единое окно общения со всех каналов."""
    import routes_shop
    return routes_shop.inbox_page(request, ADMIN)


@app.get(ADMIN + "/products", response_class=HTMLResponse)
def admin_products(request: Request):
    """Товары и цены — редактируются здесь, а не в HTML."""
    user = require_admin(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db.pool.connection() as conn:
        products = conn.execute("SELECT * FROM products ORDER BY sort_order, id").fetchall()
    return render(request, "admin_products.html", user=user, products=products)


@app.get(ADMIN + "/settings", response_class=HTMLResponse)
def admin_settings(request: Request):
    """Настройки интеграций: Telegram, VK. Токены — зашифрованы в БД."""
    user = require_admin(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    import secrets_helper
    s = db.all_settings("telegram.") | db.all_settings("vk.") | db.all_settings("site.")
    return render(
        request, "admin_settings.html", user=user, settings=s,
        tg_token_mask=secrets_helper.mask(secrets_helper.decrypt(str(s.get("telegram.token", "")))),
        vk_token_mask=secrets_helper.mask(secrets_helper.decrypt(str(s.get("vk.token", "")))),
    )


@app.post(ADMIN + "/settings")
def save_settings(
    request: Request,
    telegram_token: str = Form(""),
    telegram_proxy: str = Form(""),
    telegram_chat: str = Form(""),
    vk_token: str = Form(""),
    site_phone: str = Form(""),
    site_email: str = Form(""),
):
    """Сохранение интеграций. Пустое поле токена = не менять."""
    user = require_admin(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    import secrets_helper
    if telegram_token.strip():
        db.set_setting("telegram.token", secrets_helper.encrypt(telegram_token.strip()), user["id"])
    db.set_setting("telegram.proxy", telegram_proxy.strip(), user["id"])
    db.set_setting("telegram.chat", telegram_chat.strip(), user["id"])
    if vk_token.strip():
        db.set_setting("vk.token", secrets_helper.encrypt(vk_token.strip()), user["id"])
    db.set_setting("site.phone", site_phone.strip(), user["id"])
    db.set_setting("site.email", site_email.strip(), user["id"])
    return RedirectResponse(ADMIN + "/settings?saved=1", status_code=303)
