"""Админка: клиенты, их заказы, проекты и все фото.

Структура, которую видит администратор:

    Клиент (Аляева Елена, +79028759387)
      └─ Заказ AA-261123-001  «Именная книга»
           └─ Проект «Книга для Ани»
                └─ фото: orig_name, размер, дата

Папки на диске: хранилище/<user_id>/<project_id>/<файл> или guest/<project_id>/<файл>
для старых гостевых. Админ видит ВСЕ файлы, клиент — только свои.
"""
from __future__ import annotations

import html
import os

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from urllib.parse import quote

import db
from web_common import render

router = APIRouter()

from order_status import STATUS_NAMES as STATUSES, add_event  # один словарь на весь проект


def _esc(value) -> str:
    """Экранирование пользовательских данных в HTML."""
    return html.escape(str(value if value is not None else ""))


def admin_only(request: Request):
    """Администратор или понятный отказ.

    Не залогинен → на вход (чтобы войти под админом).
    Залогинен, но не админ → страница «доступ запрещён», а не молчаливый
    выброс в кабинет: человек должен понимать, почему его не пустили.
    """
    user = db.session_user(request.cookies.get("sid", ""))
    if not user:
        return None, RedirectResponse(
            "/login?next=" + quote(request.url.path), status_code=303)
    if user.get("role") != "admin":
        resp = render(request, "403.html", user=user, who=user.get("email"))
        resp.status_code = 403
        return None, resp
    return user, None


CLIENTS_SQL = """
SELECT u.id, u.email, u.full_name, u.phone, u.role, u.created_at,
       (SELECT count(*) FROM orders o WHERE o.user_id = u.id) AS orders_count,
       (SELECT coalesce(sum(o.total_rub), 0) FROM orders o WHERE o.user_id = u.id) AS total_rub,
       (SELECT max(o.created_at) FROM orders o WHERE o.user_id = u.id) AS last_order_at,
       (SELECT count(*) FROM project_files f WHERE f.user_id = u.id) AS files_count
FROM users u
WHERE u.role <> 'admin'
ORDER BY (SELECT max(o.created_at) FROM orders o WHERE o.user_id = u.id) DESC NULLS LAST, u.created_at DESC
"""


@router.get("/admin/clients", response_class=HTMLResponse)
def clients_page(request: Request):
    """Все клиенты: заказы, суммы, фото. Поиск по имени, почте, телефону."""
    user, redirect = admin_only(request)
    if redirect:
        return redirect
    q = (request.query_params.get("q") or "").strip()
    with db.pool.connection() as conn:
        if q:
            like = "%" + q.lower() + "%"
            rows = conn.execute(
                CLIENTS_SQL.replace(
                    "WHERE u.role <> 'admin'",
                    "WHERE u.role <> 'admin' AND (lower(coalesce(u.email,'')) LIKE %s "
                    "OR lower(coalesce(u.full_name,'')) LIKE %s OR coalesce(u.phone,'') LIKE %s)",
                ).replace("NULLS LAST", "NULLS LAST").replace(
                    "ORDER BY", "ORDER BY", 1
                ),
                (like, like, "%" + q + "%"),
            ).fetchall()
        else:
            rows = conn.execute(CLIENTS_SQL).fetchall()
    return render(request, "admin_clients.html", user=user, clients=rows, q=q)


@router.get("/admin/client/{user_id}", response_class=HTMLResponse)
def client_card(request: Request, user_id: int):
    """Карточка клиента: данные, заказы, проекты, все фото."""
    user, redirect = admin_only(request)
    if redirect:
        return redirect
    with db.pool.connection() as conn:
        client = conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
        if not client:
            return HTMLResponse("Клиент не найден", status_code=404)
        orders = conn.execute(
            """SELECT o.*, (SELECT count(*) FROM projects p WHERE p.order_id = o.id) AS projects
               FROM orders o WHERE o.user_id = %s ORDER BY o.created_at DESC""",
            (user_id,),
        ).fetchall()
        projects = conn.execute(
            """SELECT p.*, o.number AS order_number,
                 (SELECT count(*) FROM project_files f WHERE f.project_id = p.id) AS files
               FROM projects p LEFT JOIN orders o ON o.id = p.order_id
               WHERE p.user_id = %s ORDER BY p.created_at DESC""",
            (user_id,),
        ).fetchall()
        # фото: ВСЕ проекты этого клиента (у гостевых владелец уже привязан)
        files = conn.execute(
            """SELECT f.*, p.title AS project_title
               FROM project_files f LEFT JOIN projects p ON p.id = f.project_id
               WHERE p.user_id = %s ORDER BY f.created_at DESC""",
            (user_id,),
        ).fetchall()
    return render(request, "admin_client.html", user=user, client=client,
                  orders=orders, projects=projects, files=files, statuses=STATUSES)


@router.get("/admin/photos", response_class=HTMLResponse)
def all_photos(request: Request):
    """Все фото всех клиентов — с фильтром по клиенту и заказу."""
    user, redirect = admin_only(request)
    if redirect:
        return redirect
    client_id = request.query_params.get("client")
    with db.pool.connection() as conn:
        clients = conn.execute(
            """SELECT DISTINCT u.id, u.email, u.full_name
               FROM users u JOIN project_files f ON f.user_id = u.id
               WHERE u.role <> 'admin' ORDER BY u.full_name"""
        ).fetchall()
        sql = """SELECT f.*, p.title AS project_title, o.number AS order_number,
                        u.email AS client_email, u.full_name AS client_name
                 FROM project_files f
                 LEFT JOIN projects p ON p.id = f.project_id
                 LEFT JOIN orders o ON o.id = p.order_id
                 LEFT JOIN users u ON u.id = f.user_id
                 %s
                 ORDER BY f.created_at DESC LIMIT 300"""
        where = ""
        params = ()
        if client_id:
            where = "WHERE f.user_id = %s"
            params = (int(client_id),)
        files = conn.execute(sql % where, params).fetchall()
    return render(request, "admin_photos.html", user=user, files=files,
                  clients=clients, selected=int(client_id) if client_id else 0)


@router.post("/api/admin/order/{order_id}/status")
def set_order_status(request: Request, order_id: int, status: str = Form("")):
    """Смена статуса заказа без перезагрузки страницы."""
    user, redirect = admin_only(request)
    if redirect:
        return JSONResponse({"ok": False, "error": "Нужно войти"}, status_code=401)
    if status not in STATUSES:
        return JSONResponse({"ok": False, "error": "Неизвестный статус"}, status_code=400)
    with db.pool.connection() as conn:
        row = conn.execute("SELECT id, number, user_id FROM orders WHERE id = %s", (order_id,)).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "Заказ не найден"}, status_code=404)
        # Старое значение нужно, чтобы не писать в ленту «сменился» на тот же самый статус.
        old = conn.execute("SELECT status FROM orders WHERE id = %s", (order_id,)).fetchone()
        conn.execute(
            "UPDATE orders SET status = %s, updated_at = now() WHERE id = %s", (status, order_id)
        )
        # Пишем только реальную смену: клик по текущему статусу — не событие.
        if not old or old["status"] != status:
            add_event(conn, order_id, "status", status=status, author="admin")
            # Клиенту не нужно самому гадать, на каком этапе книга — он получит новость (задача #21).
            import notifications
            notifications.notify(
                conn, row["user_id"], "order_status",
                f"Заказ {row['number']}: {STATUSES[status]}",
                "Подробности и история — в карточке заказа.",
                f"/cabinet/order/{order_id}",
            )
        conn.commit()
    return JSONResponse({"ok": True, "status": status, "label": STATUSES[status]})


@router.post("/api/admin/project/create")
def create_project(request: Request, user_id: int = Form(...), title: str = Form(""),
                   order_id: str = Form("")):
    """Завести проект (папку) клиенту из админки — например, когда заказ обсуждали в переписке."""
    user, redirect = admin_only(request)
    if redirect:
        return JSONResponse({"ok": False, "error": "Нужно войти"}, status_code=401)
    title = (title or "").strip() or "Проект"
    oid = int(order_id) if (order_id or "").strip().isdigit() else None
    with db.pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO projects (user_id, order_id, title) VALUES (%s, %s, %s) RETURNING id",
            (user_id, oid, title),
        ).fetchone()
        conn.commit()
    return JSONResponse({"ok": True, "id": row["id"], "title": title})
