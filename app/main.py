"""alyaeva.pro — приложение: авторизация, личный кабинет, админка.

Запуск: uvicorn main:app --host 0.0.0.0 --port 8000
Статика сайта отдаётся nginx'ом напрямую, сюда идут только динамические пути.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import JSONResponse
from urllib.parse import quote
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
from order_status import STATUS_ORDER, STAGE_NAMES
from routes_shop import router as shop_router, status_name
from admin_clients import router as admin_clients_router
from hooks import router as hooks_router  # noqa: E402
app.include_router(admin_clients_router)
app.include_router(hooks_router)
app.include_router(shop_router)


@app.get("/health")
def health():
    """Проверка живости приложения вместе с базой.

    Раньше возвращала 200 даже при мёртвой БД — деплой и сторож принимали
    нерабочее приложение за живое. Теперь ошибка БД = 503.
    """
    try:
        with db.pool.connection() as conn:
            conn.execute("SELECT 1")
        return {"ok": True, "db": "up"}
    except Exception as e:
        return JSONResponse({"ok": False, "db": "down", "error": str(e)}, status_code=503)


# ---------------- Вход / выход ----------------

# Secure-флаг: на проде всегда https, на стенде по http — иначе браузер cookie отбросит.
_COOKIE_SECURE = os.getenv("COOKIE_SECURE", "1") == "1"

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = "", next: str = "", reason: str = "", email: str = ""):
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
                  next=_safe_next(next, ""), reason=reason, email=email)


def _safe_next(value: str, default: str) -> str:
    """Куда вернуть после входа. Только внутренние адреса — иначе открытый редирект."""
    value = (value or "").strip()
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return default
    return value


@app.get("/ceny", response_class=HTMLResponse)
def prices_page(request: Request):
    """Публичная таблица цен — сюда ведёт кнопка «Узнать стоимость».

    Берём те же товары, что и админка: цена правится в одном месте,
    и на сайте, и в кабинете показывается одно и то же.
    """
    with db.pool.connection() as conn:
        rows = conn.execute(
            """SELECT title, description, price_rub, old_price
               FROM products WHERE is_active ORDER BY sort_order, id"""
        ).fetchall()
    return render(request, "prices.html", products=rows)


@app.get("/order", response_class=HTMLResponse)
def order_page(request: Request):
    """Страница заказа. Заказывать могут только вошедшие — так заказ сразу привязан к клиенту.

    Гость уходит на вход с пометкой, что шёл за заказом, и после входа попадает сюда же.
    """
    user = current_user(request)
    if not user:
        return RedirectResponse("/login?next=/order&reason=order", status_code=303)
    return render(request, "order.html", user=user)


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...),
          next: str = Form("")):
    """Проверка логина и пароля, выдача cookie-сессии."""
    raw_email = (email or "").strip()
    user = db.find_user_by_email(raw_email)
    # Диагностика: в лог пишем ТОЧНУЮ причину, человеку — одну общую фразу.
    if not user:
        _log_login(raw_email, False, request, why="почта не найдена")
    else:
        ok = db.verify_password(password, user["password_hash"])
        _log_login(raw_email, ok, request,
                   why="ок" if ok else f"пароль не подошёл (id={user['id']})")
    if not user or not ok:
        # Подсказки по частым причинам — не раскрывая, есть ли такой аккаунт.
        hint = ""
        if raw_email != raw_email.lower() or raw_email != raw_email.strip():
            hint = "Почта вводится без учёта регистра — регистр не важен."
        elif password != password.strip():
            hint = "Похоже, в пароле оказался пробел в начале или в конце."
        return render(request, "login.html", error="Неверный e-mail или пароль", user=None,
                      next=_safe_next(next, ""), reason="", email=raw_email, hint=hint)
    token = db.create_session(
        user["id"],
        ua=request.headers.get("user-agent", ""),
        ip=request.client.host if request.client else "",
    )
    # Админ всё равно идёт в админку, но если он явно шёл заказывать — ведём к заказу.
    wanted = _safe_next(next, "")
    target = wanted or (ADMIN if user["role"] == "admin" else "/cabinet")
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie("sid", token, httponly=True, secure=_COOKIE_SECURE, samesite="lax", max_age=30 * 86400)
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


def _log_login(email: str, ok: bool, request: Request, why: str = "") -> None:
    """Отметка о попытке входа. Пароль не пишем никогда — только факт и причину отказа.

    Без причины «не пускает» приходилось угадывать: почта не та, пароль не тот
    или человек завёл второй аккаунт на другой адрес.
    """
    ip = request.client.host if request.client else ""
    tail = f" why={why}" if why else ""
    print(f"[login] {'ok' if ok else 'FAIL'} {email.strip().lower()} ip={ip}{tail}", flush=True)


# ---------------- Личный кабинет ----------------

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    """Настройки пользователя: смена пароля и данные аккаунта."""
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    return render(request, "settings.html", user=user)


@app.post("/api/password")
def api_change_password(request: Request, old_password: str = Form(""),
                        new_password: str = Form("")):
    """Смена пароля. Ответ — JSON, без перезагрузки страницы."""
    user = db.session_user(request.cookies.get("sid", ""))
    if not user:
        return JSONResponse({"ok": False, "error": "Нужно войти", "login": True}, status_code=401)
    ok, message = db.change_password(user["id"], old_password, new_password)
    return JSONResponse({"ok": ok, "error": None if ok else message})


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
        # Ошибка на ТОЙ ЖЕ странице: человек не должен заполнять форму заново.
        return render(request, "register.html", user=None,
                      error="Проверьте почту и пароль — пароль минимум 8 символов.",
                      email=email, full_name=full_name.strip(), phone=phone.strip(),
                      next=_safe_next(next, ""))
    if db.find_user_by_email(email):
        return render(request, "register.html", user=None,
                      error="Аккаунт с такой почтой уже есть. Войдите под ним — пароль тот, что вы задали при регистрации.",
                      email=email, full_name=full_name.strip(), phone=phone.strip(),
                      next=_safe_next(next, ""), exists=True)
    try:
        uid = db.create_user(email, password, full_name.strip(), "customer", phone.strip())
    except db.DuplicateEmail:
        return render(request, "register.html", user=None,
                      error="Аккаунт с такой почтой уже есть. Войдите под ним — пароль тот, что вы задали при регистрации.",
                      email=email, full_name=full_name.strip(), phone=phone.strip(),
                      next=_safe_next(next, ""), exists=True)
    # Заказы, оформленные гостем с этим телефоном, становились «ничьими» —
    # теперь у них есть владелец, и фото из guest/ попадают в его кабинет.
    if phone.strip():
        orders_mod.claim_guest_orders(uid, phone.strip(), email)
    token = db.create_session(uid, ua=request.headers.get("user-agent", ""),
                              ip=request.client.host if request.client else "")
    resp = RedirectResponse(_safe_next(next, "/cabinet"), status_code=303)
    resp.set_cookie("sid", token, httponly=True, secure=_COOKIE_SECURE, samesite="lax", max_age=30 * 86400)
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
                  photos_by_order=photos_by_order, status_name=status_name,
                  photos_need=[o["id"] for o in orders if not photos_by_order.get(o["id"])],
                  stages=STATUS_ORDER, stage_names=STAGE_NAMES)


@app.get("/cabinet/repeat/{order_id}")
def cabinet_repeat(order_id: int, request: Request):
    """Повторить заказ — ведём на форму с уже заполненными пожеланиями.

    Магазины считают повтор заказа главным ускорителем второй покупки:
    человек не набирает всё заново. Свои фото он загружает на странице заказа.
    """
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    with db.pool.connection() as conn:
        row = conn.execute(
            "SELECT id, comment FROM orders WHERE id = %s AND user_id = %s",
            (order_id, user["id"]),
        ).fetchone()
    if not row:
        return _deny(request)
    from urllib.parse import quote
    same = quote(row["comment"] or "")
    return RedirectResponse(f"/order?repeat={order_id}&comment={same}", status_code=303)


@app.get("/cabinet/order/{order_id}", response_class=HTMLResponse)
def cabinet_order(order_id: int, request: Request):
    """Один заказ клиента: фото крупно и в полном размере.

    Владелец проверяется по user_id из сессии. Чужой заказ не открывается
    ни под каким видом — номер заказа НЕ является ключом доступа.
    """
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    with db.pool.connection() as conn:
        row = conn.execute(
            """SELECT id, number, status, total_rub, created_at, comment
               FROM orders WHERE id = %s AND user_id = %s""",
            (order_id, user["id"]),
        ).fetchone()
        if not row:
            return _deny(request)
        photos = conn.execute(
            """SELECT f.id, f.orig_name, f.size_bytes, f.created_at
               FROM project_files f JOIN projects p ON p.id = f.project_id
               WHERE p.order_id = %s ORDER BY f.created_at""",
            (order_id,),
        ).fetchall()
    return render(request, "cabinet_order.html", user=user, order=row,
                  photos=photos, status_name=status_name,
                  stages=STATUS_ORDER, stage_names=STAGE_NAMES)


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

# Старый секретный слаг. Уводим на нормальный адрес: ссылки, что владелец
# сохранил в закладках, не должны отдавать 404.
OLD_ADMIN = "/6b03c5256535e9ab5a"


@app.get(OLD_ADMIN + "{rest:path}")
def old_admin_redirect(rest: str):
    """Любой путь под старым слагом → на /admin."""
    return RedirectResponse(ADMIN + rest, status_code=301)


def _deny(request: Request):
    """Отказ в доступе: не залогинен — на вход, залогинен — страница «доступ закрыт»."""
    person = current_user(request)
    if not person:
        return RedirectResponse("/login?next=" + quote(request.url.path), status_code=303)
    resp = render(request, "403.html", user=person, who=person.get("email"))
    resp.status_code = 403
    return resp


@app.get(ADMIN, response_class=HTMLResponse)
def admin_orders(request: Request):
    """Заказы: список для администратора."""
    user = require_admin(request)
    if not user:
        return _deny(request)
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
        return _deny(request)
    with db.pool.connection() as conn:
        products = conn.execute("SELECT * FROM products ORDER BY sort_order, id").fetchall()
    return render(request, "admin_products.html", user=user, products=products)


@app.get(ADMIN + "/settings", response_class=HTMLResponse)
def admin_settings(request: Request):
    """Настройки САЙТА: контакты и текст подвала.

    Мессенджеры здесь не настраиваются — они на /admin/hooks.
    """
    user = require_admin(request)
    if not user:
        return _deny(request)
    s = db.all_settings("site.")
    return render(request, "admin_settings.html", user=user, settings=s,
                  saved=request.query_params.get("saved") == "1")


@app.post(ADMIN + "/settings")
def save_settings(
    request: Request,
    site_phone: str = Form(""),
    site_email: str = Form(""),
    site_address: str = Form(""),
    site_work_hours: str = Form(""),
    site_instagram: str = Form(""),
    site_vk: str = Form(""),
    site_telegram: str = Form(""),
    site_whatsapp: str = Form(""),
    site_footer: str = Form(""),
):
    """Настройки САЙТА: контакты, соцсети, подвал.

    Мессенджеры здесь не настраиваются — только /admin/hooks,
    чтобы одна настройка не лежала в двух местах.
    """
    user = require_admin(request)
    if not user:
        return _deny(request)
    pairs = {
        "site.phone": site_phone,
        "site.email": site_email,
        "site.address": site_address,
        "site.work_hours": site_work_hours,
        "site.instagram": site_instagram,
        "site.vk": site_vk,
        "site.telegram": site_telegram,
        "site.whatsapp": site_whatsapp,
        "site.footer": site_footer,
    }
    for key, val in pairs.items():
        db.set_setting(key, val.strip(), user["id"])
    return RedirectResponse(ADMIN + "/settings?saved=1", status_code=303)


# ---------------- Страницы ошибок ----------------
# Раньше несуществующий путь приложения отдавал голый {"detail":"Not Found"} —
# человек видел служебный JSON вместо страницы. Теперь — страница в стиле сайта.

from fastapi.exceptions import RequestValidationError as _RVE
from starlette.exceptions import HTTPException as _HTTPExc


def _wants_json(request: Request) -> bool:
    """Запрос от скрипта/бота, а не из браузера? Тогда отвечаем JSON.

    Раньше всё под /api/ и /hooks/ безусловно отдавало JSON — и человек,
    открывший адрес в браузере, видел {"detail":"Not Found"}.
    Смотрим Accept: браузер просит text/html, fetch — */* или json.
    """
    accept = (request.headers.get("accept") or "").lower()
    # Браузер присылает "text/html,..."; curl по умолчанию — "*/*"; fetch — часто "*/*".
    # Значит страницу даём только тем, кто ЯВНО просит html.
    return "text/html" not in accept


@app.exception_handler(_HTTPExc)
async def _http_error(request: Request, exc: _HTTPExc):
    """404 → страница «не найдено»; 403 → «сюда нельзя»; остальное — честный код."""
    if _wants_json(request):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    if exc.status_code not in (403, 404, 405, 422):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    user = current_user(request)
    if exc.status_code == 403:
        resp = render(request, "403.html", user=user,
                      who=(user.get("email") if user else None))
        resp.status_code = 403
        return resp
    if exc.status_code == 405:
        # Адрес есть, но открыт не тем способом (например, ссылку на действие
        # нажали, а обработчик ждёт POST). Человеку — страница, а не «Method Not Allowed».
        resp = render(request, "404.html", user=user)
        resp.status_code = 404
        return resp
    resp = render(request, "404.html", user=user)
    resp.status_code = 404
    return resp


@app.exception_handler(_RVE)
async def _validation_error(request: Request, exc: _RVE):
    """Кривые данные от формы: человеку — понятный текст, а не список полей Pydantic."""
    if _wants_json(request):
        return JSONResponse({"detail": exc.errors()}, status_code=422)
    resp = render(request, "404.html", user=current_user(request))
    resp.status_code = 404
    return resp
