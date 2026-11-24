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
import payments
import secrets_helper
from config import config

from web_common import ADMIN, current_user, pages, render, require_admin, require_user, site_links, templates  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Открыть/закрыть пул БД вместе с приложением."""
    db.open_pool()
    # Напоминания о заказах без фото — сразу после того, как база доступна.
    try:
        import reminders
        reminders.start()
    except Exception:  # noqa: BLE001 — фон не должен мешать сайту подняться
        logging.getLogger("main").warning("напоминания не запущены", exc_info=True)
    yield
    db.close_pool()


app = FastAPI(title="alyaeva.pro", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

import orders as orders_mod  # noqa: E402
import yukassa  # noqa: E402
from order_status import STATUS_ORDER, STAGE_NAMES, STATUS_NAMES, ACTIVE_STATUSES, next_step
from routes_shop import router as shop_router, status_name
from admin_clients import router as admin_clients_router
from admin_posts import router as admin_posts_router
from hooks import router as hooks_router  # noqa: E402
app.include_router(admin_clients_router)
app.include_router(admin_posts_router)
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

@app.get("/", response_class=HTMLResponse)
def home_page(request: Request):
    """Главная — из админки: телефон и мессенджеры подставляются, а не лежат копиями в файле.

    Статический html/index.html остаётся как есть (nginx отдаёт его напрямую); приложение рендерит
    свой шаблон `pages/web/index.html`. Смена контакта в /admin/settings меняет и шапку, и подвал.
    """
    return pages.TemplateResponse(
        request, "index.html",
        {"links": site_links(), "user": current_user(request), "pay_available": payments.available()},
        headers={"Cache-Control": "no-cache"},
    )

@app.get("/fotokniga-ekaterinburg/", response_class=HTMLResponse)
@app.get("/fotokniga-ekaterinburg", response_class=HTMLResponse)
def fotokniga_page(request: Request):
    """Лендинг фотокниги — контакты из админки, как на главной."""
    return pages.TemplateResponse(
        request, "fotokniga-ekaterinburg.html",
        {"links": site_links(), "user": current_user(request), "pay_available": payments.available()},
        headers={"Cache-Control": "no-cache"},
    )

@app.get("/oplata", response_class=HTMLResponse)
def pay_page(request: Request):
    """Как оплатить. Показываем только включённые и заполненные способы (#122)."""
    blocks = payments.ready()
    return render(request, "oplata.html", user=current_user(request), blocks=blocks,
                  pay_state=payments.state(),
                  pay={b: payments.details(b) for b in payments.BLOCKS})

@app.get("/policy", response_class=HTMLResponse)
def policy_page(request: Request):
    """Политика обработки персональных данных (152-ФЗ).

    Текст лежит в шаблоне, а реквизиты оператора — в настройках сайта:
    владелец меняет ИНН в админке, а не в коде. Одна настройка — одно место.
    """
    import datetime
    return render(
        request, "policy.html",
        policy_date=datetime.date.today().strftime("%d.%m.%Y"),
        operator_name=db.get_setting("site.operator_name", "") or "Аляева Алёна",
        operator_requisites=db.get_setting("site.operator_requisites", "") or "",
        keep_photo_months=db.get_setting("site.keep_photo_months", "") or "12",
        contacts=", ".join(x for x in (
            str(db.get_setting("site.email", "") or ""), str(db.get_setting("site.phone", "") or "")
        ) if x),
    )


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
    import pricing
    return render(request, "prices.html", products=rows, formats=pricing.FORMATS)


# Какие статические страницы сайта можно отметить в счётчике.
# Белый список, а не свободный текст: иначе в базу можно налить любые строки.
STATIC_PAGES = {"/": "Главная", "/ceny": "Стоимость", "/404": "Не найдено"}


@app.get("/api/visited")
def api_visited(request: Request):
    """Счётчик для главной: страница статическая, приложение её не видит.

    Принимает только название страницы из белого списка и ничего больше.
    Свои проверки помечаются заголовком X-DSH-Check: 1 — их не считаем, иначе
    данные превращаются в «81 просмотр за час» от собственных тестов.
    """
    if request.headers.get("x-dsh-check") == "1":
        return {"ok": True, "skipped": "check"}
    page = (request.query_params.get("p") or "")[:60]
    if page not in STATIC_PAGES:
        return {"ok": False, "error": "unknown page"}
    referer = request.headers.get("referer", "")
    import analytics
    analytics.track("view", page, analytics.source_from_referer(referer))
    return {"ok": True}


@app.get("/api/price")
def api_price(format: str = "a4", pages: int = 16):
    """Вилка цены для калькулятора на странице цен.

    Правила считает app/pricing.py — те же, что увидит человек в форме заказа.
    """
    import pricing
    d = pricing.calc(format, pages)
    return JSONResponse({"ok": True, **d})


@app.get("/order", response_class=HTMLResponse)
def order_page(request: Request):
    """Страница заказа. Заказывать могут только вошедшие — так заказ сразу привязан к клиенту.

    Гость уходит на вход с пометкой, что шёл за заказом, и после входа попадает сюда же.
    """
    user = current_user(request)
    if not user:
        return RedirectResponse("/login?next=/order&reason=order", status_code=303)
    import analytics
    analytics.track("order_open", "/order")
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
             full_name: str = Form(""), phone: str = Form(""), next: str = Form(""),
             consent: str = Form(""), csrf: str = Form("")):
    """Регистрация клиента. Гостевые заказы с тем же телефоном переходят к нему."""
    # Залогиненному форма обязана быть подписана: без подписи это подделка.
    # Гостю ничего не мешает — у него нет сессии, которой можно воспользоваться.
    if current_user(request) and not csrf:
        return deny_csrf(request)
    # Согласие проверяем на сервере: галочку в HTML легко снять запросом из консоли,
    # а собирать персональные данные без основания нельзя (152-ФЗ).
    if not consent:
        return render(request, "register.html", user=None, next=_safe_next(next, ""),
                      error="Чтобы зарегистрироваться, нужно согласие на обработку персональных данных")
    email = (email or "").strip().lower()
    if not email or len(password or "") < 8:
        # Ошибка на ТОЙ ЖЕ странице: человек не должен заполнять форму заново.
        return render(request, "register.html", user=None, next=_safe_next(next, ""),
                      error="Проверьте почту и пароль — пароль минимум 8 символов.",
                      email=email, full_name=full_name.strip(), phone=phone.strip())
    if db.find_user_by_email(email):
        return render(request, "register.html", user=None, next=_safe_next(next, ""),
                      error="Аккаунт с такой почтой уже есть. Войдите под ним — пароль тот, что вы задали при регистрации.",
                      email=email, full_name=full_name.strip(), phone=phone.strip(),
                      exists=True)
    try:
        uid = db.create_user(email, password, full_name.strip(), "customer", phone.strip())
    except db.DuplicateEmail:
        return render(request, "register.html", user=None, next=_safe_next(next, ""),
                      error="Аккаунт с такой почтой уже есть. Войдите под ним — пароль тот, что вы задали при регистрации.",
                      email=email, full_name=full_name.strip(), phone=phone.strip(),
                      exists=True)
    # Заказы, оформленные гостем с этим телефоном, становились «ничьими» —
    # теперь у них есть владелец, и фото из guest/ попадают в его кабинет.
    if phone.strip():
        orders_mod.claim_guest_orders(uid, phone.strip(), email)
    import analytics
    analytics.track("register", "/register")
    token = db.create_session(uid, ua=request.headers.get("user-agent", ""),
                              ip=request.client.host if request.client else "")
    resp = RedirectResponse(_safe_next(next, "/cabinet"), status_code=303)
    resp.set_cookie("sid", token, httponly=True, secure=_COOKIE_SECURE, samesite="lax", max_age=30 * 86400)
    return resp


@app.get("/cabinet/notifications", response_class=HTMLResponse)
def cabinet_notifications(request: Request):
    """Новости о заказах человека — без переписки и без захода в каждую карточку."""
    user = current_user(request)
    if not user:
        return RedirectResponse("/login?next=/cabinet/notifications", status_code=303)
    import notifications
    with db.pool.connection() as conn:
        items = notifications.list_for(conn, user["id"])
        # Открыли страницу — значит увидели. Отмечаем сразу, иначе точка в шапке не погаснет.
        notifications.mark_read(conn, user["id"])
    return render(request, "cabinet_notifications.html", user=user, list=items)


@app.post("/api/notifications/read")
def api_notifications_read(request: Request):
    """Отметить прочитанным. Вызывается fetch'ем — страница не перезагружается."""
    user = current_user(request)
    if not user:
        return JSONResponse({"ok": False, "login": True}, status_code=401)
    import notifications
    with db.pool.connection() as conn:
        notifications.mark_read(conn, user["id"])
    return JSONResponse({"ok": True})


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
                  stages=STATUS_ORDER, stage_names=STAGE_NAMES, active_statuses=ACTIVE_STATUSES)


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
        events = conn.execute(
            """SELECT kind, status, body, created_at FROM order_events
               WHERE order_id = %s ORDER BY created_at, id""",
            (order_id,),
        ).fetchall()
    # Способы оплаты в карточке заказа: все включённые и заполненные блоки (#122).
    # Раньше здесь была только кнопка ЮKassa — включённый счёт или ссылка в кабинете не показывались.
    # Оплаченный заказ и заказ без суммы выбора не получают: платить там нечего.
    pay_blocks = [] if row["status"] == "paid" or not (row["total_rub"] or 0) else payments.ready()
    return render(request, "cabinet_order.html", user=user, order=row, events=events,
                  status_names=STATUS_NAMES, pay_blocks=pay_blocks,
                  pay_state=payments.state(),
                  pay={b: payments.details(b) for b in payments.BLOCKS},
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
    return render(request, "admin_orders.html", user=user, orders=orders, status_names=STATUS_NAMES)


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


@app.get(ADMIN + "/stats", response_class=HTMLResponse)
def admin_stats(request: Request, days: int = 30):
    """Сколько зашли, сколько открыли заказ, сколько оформили, откуда пришли.

    Свои счётчики в БД: данные наши, 152-ФЗ не затрагивается — личности мы не пишем.
    """
    user = require_admin(request)
    if not user:
        return _deny(request)
    import analytics
    days = max(1, min(days, 365))
    return render(request, "admin_stats.html", days=days, s=analytics.summary(days),
                  SOURCE_NAMES=analytics.SOURCE_NAMES)


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
    site_operator_name: str = Form(""),
    site_operator_requisites: str = Form(""),
    site_keep_photo_months: str = Form(""),
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
        "site.operator_name": site_operator_name,
        "site.operator_requisites": site_operator_requisites,
        "site.keep_photo_months": site_keep_photo_months,
    }
    # Сохранение из формы без JS по-прежнему уводит на ту же страницу с плашкой (#215).
    wants_json = (request.headers.get("accept") or "").find("application/json") >= 0
    for key, val in pairs.items():
        db.set_setting(key, val.strip(), user["id"])
    if wants_json:
        # Сообщение без перезагрузки: страница остаётся на месте, прокрутка не сбрасывается.
        return JSONResponse({"ok": True, "saved": "настройки сайта"})
    return RedirectResponse(ADMIN + "/settings?saved=1", status_code=303)


@app.get(ADMIN + "/pay", response_class=HTMLResponse)
def admin_pay(request: Request):
    """Оплата: три способа в одном месте (#122). Мессенджеры — на /admin/hooks, контакты — на /admin/settings."""
    user = require_admin(request)
    if not user:
        return _deny(request)
    st = payments.state()
    secret = payments._value("pay.yukassa.secret")
    return render(request, "admin_pay.html", user=user,
                  settings=db.all_settings("pay."), pay_state=st, pay_ready=payments.ready(),
                  yukassa_mask=("задан ···· " + secret[-4:]) if len(secret) > 4 else "не задан",
                  yukassa_webhook=payments.webhook_url(),
                  saved=request.query_params.get("saved") == "1")


@app.post(ADMIN + "/pay")
def save_pay(
    request: Request,
    pay_bank_on: str = Form(""), pay_bank_recipient: str = Form(""), pay_bank_account: str = Form(""),
    pay_bank_bank_name: str = Form(""), pay_bank_sbp_phone: str = Form(""), pay_bank_note: str = Form(""),
    pay_link_on: str = Form(""), pay_link_url: str = Form(""), pay_link_note: str = Form(""),
    pay_yukassa_on: str = Form(""), pay_yukassa_shop_id: str = Form(""),
    pay_yukassa_secret: str = Form(""), pay_yukassa_note: str = Form(""),
):
    """Сохранение настроек оплаты. Пустой секретный ключ — «не менять»."""
    user = require_admin(request)
    if not user:
        return _deny(request)
    pairs = {
        "pay.bank.on": "1" if pay_bank_on else "",
        "pay.bank.recipient": pay_bank_recipient,
        "pay.bank.account": pay_bank_account,
        "pay.bank.bank_name": pay_bank_bank_name,
        "pay.bank.sbp_phone": pay_bank_sbp_phone,
        "pay.bank.note": pay_bank_note,
        "pay.link.on": "1" if pay_link_on else "",
        "pay.link.url": pay_link_url,
        "pay.link.note": pay_link_note,
        "pay.yukassa.on": "1" if pay_yukassa_on else "",
        "pay.yukassa.shop_id": pay_yukassa_shop_id,
        "pay.yukassa.note": pay_yukassa_note,
    }
    for key, val in pairs.items():
        db.set_setting(key, val.strip(), user["id"])
    # Ключ пишем только если его вписали: иначе каждое сохранение затирало бы секрет пустотой.
    if pay_yukassa_secret.strip():
        db.set_setting("pay.yukassa.secret", secrets_helper.encrypt(pay_yukassa_secret.strip()), user["id"])
    wants_json = (request.headers.get("accept") or "").find("application/json") >= 0
    if wants_json:
        return JSONResponse({"ok": True, "saved": "оплата", "ready": payments.ready()})
    return RedirectResponse(ADMIN + "/pay?saved=1", status_code=303)

@app.post("/hooks/yukassa")
async def yukassa_hook(request: Request):
    """Уведомление ЮKassa. Отвечаем 200 всегда, иначе сервис будет повторять бесконечно."""
    raw = await request.body()
    payload = yukassa.parse(raw)
    if not payload:
        return JSONResponse({"ok": False}, status_code=400)
    code, note = yukassa.handle_notification(payload)
    return JSONResponse({"ok": code == 200, "note": note}, status_code=code)


@app.post("/api/pay/{order_id}")
def api_pay(order_id: int, request: Request):
    """Создать платёж по заказу и вернуть ссылку. Только для своего заказа."""
    user = current_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "Нужно войти", "login": True}, status_code=401)
    if not yukassa.enabled():
        return JSONResponse({"ok": False, "error": "Оплата картой не настроена"}, status_code=400)
    with db.pool.connection() as conn:
        order = conn.execute(
            "SELECT * FROM orders WHERE id = %s AND user_id = %s", (order_id, user["id"])
        ).fetchone()
    if not order:
        return JSONResponse({"ok": False, "error": "Заказ не найден"}, status_code=404)
    if order["status"] == "paid":
        return JSONResponse({"ok": False, "error": "Заказ уже оплачен"}, status_code=400)
    res = yukassa.create_payment(dict(order))
    return JSONResponse(res, status_code=200 if res.get("ok") else 502)

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


# ---------- Защита от чужих форм (CSRF) ----------

# Действия, которые меняют данные и принимают простую отправку формы
# (application/x-www-form-urlencoded, multipart, text/plain). Именно такую форму
# чужой сайт отправляет в браузере человека, где уже лежит его сессия.
_FORM_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data", "text/plain", "")
_SAFE_METHODS = ("GET", "HEAD", "OPTIONS", "TRACE")
# Сюда стучатся мессенджеры: у них нет ни cookie, ни нашего домена.
_NO_CSRF_PREFIX = ("/hooks/",)


def _site_origins(request: Request) -> set[str]:
    """Какие домены считаем своими: текущий и alyaeva.pro (с www и без)."""
    host = (request.url.hostname or "").lower()
    names = {host, "alyaeva.pro", "www.alyaeva.pro", "alyaeva-app"}
    return {f"{scheme}://{name}" for scheme in ("https", "http") for name in names if name}


def _csrf_ok(request: Request, body: bytes) -> bool:
    """Форма пришла с нашего сайта? Проверка источника, не сессии.

    Два заслона:
    1. Origin (или Referer, если Origin не прислали) должен быть нашим доменом.
       Так отвечаем на «форму прислали с чужой страницы».
    2. Признак формы (cookie-сессия ИЛИ скрытое поле csrf=1, см. deny_csrf) — тоже
       требует своего источника. Без него проверка обходилась первой же формой
       входа: у гостя стирали признак и присылали форму с любого сайта.
    """
    if not request.cookies.get("sid"):
        return True
    origin = request.headers.get("origin", "")
    if origin:
        return origin in _site_origins(request)
    referer = request.headers.get("referer", "")
    if referer:
        return any(referer.startswith(o + "/") or referer == o for o in _site_origins(request))
    return False


def deny_csrf(request: Request):
    """Отказ «форма пришла не с нашего сайта» — страницей человеку, JSON скрипту.

    Нужен маршрутам, где человек **залогинен** (регистрация вторым аккаунтом,
    кабинет): там 403 не «защищает нечего», а закрывает подделку формы.
    """
    if _wants_json(request):
        return JSONResponse(
            {"ok": False, "error": "Форма пришла не с нашего сайта. Обновите страницу и попробуйте ещё раз."},
            status_code=403,
        )
    resp = render(request, "403.html", user=current_user(request),
                  who=(current_user(request) or {}).get("email"))
    resp.status_code = 403
    return resp


@app.middleware("http")
async def _csrf_middleware(request: Request, call_next):
    """Отказ до обработчика: чужая форма не должна дойти до кода.

    Раньше единственным заслоном был SameSite=Lax в cookie. Замер 24.11.2026:
    POST /login с Origin чужого сайта отвечал 200, /api/password с чужим Origin
    доходил до проверки сессии. Теперь такие запросы получают 403, а решение
    принимается ДО обработчика — иначе работает ненадёжно (см. ниже).

    ВАЖНО (24.11.2026): прошлая версия возвращала ответ из этого места, и обёртка
    таинственным образом его не отдавала — проверка «сработала», а клиент видел
    обычную страницу входа. Работает только одна схема: обёртка решает вопрос
    до вызова обработчика и вызывает `deny_csrf` как обычную функцию.
    """
    if request.method not in _SAFE_METHODS and not request.url.path.startswith(_NO_CSRF_PREFIX):
        ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype in _FORM_TYPES:
            body = await request.body()
            if not _csrf_ok(request, body):
                return deny_csrf(request)
            # Тело уже прочитано — кладём обратно, иначе форма придёт пустой.
            request._body = body
    return await call_next(request)

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
