"""Маршруты магазина, библиотеки фото и единого инбокса."""
from __future__ import annotations

import os

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

import db
import orders as shop
from web_common import render, require_admin, require_user, denied

router = APIRouter()

# Статусы заказа: в базе код, клиенту — понятный текст.
from order_status import STATUS_NAMES, STATUS_ORDER, status_name, add_event  # статусы и события — в одном месте



# ---------- Каталог и заказ ----------

@router.get("/api/products")
def api_products():
    """Товары с ценами для витрины (цены из БД, не из HTML)."""
    with db.pool.connection() as conn:
        rows = conn.execute(
            "SELECT id, slug, title, description, price_rub, unit FROM products WHERE is_active ORDER BY sort_order"
        ).fetchall()
    return JSONResponse([{**r, "price_rub": float(r["price_rub"])} for r in rows])


@router.post("/api/order")
def api_order(
    request: Request,
    name: str = Form(...),
    phone: str = Form(...),
    email: str = Form(""),
    comment: str = Form(""),
    product_id: int = Form(0),
    qty: int = Form(1),
    consent: str = Form(""),
):
    """Оформить заказ. Ответ — JSON, без перезагрузки страницы.

    Только для вошедших: заказ сразу принадлежит клиенту, фото идут в его папку.
    Гость получает 401 — страница заказа в этом случае отправляет на вход.
    """
    user = db.session_user(request.cookies.get("sid", ""))
    if not user:
        return JSONResponse({"ok": False, "error": "Нужно войти", "login": True}, status_code=401)
    items = []
    if product_id:
        with db.pool.connection() as conn:
            p = conn.execute("SELECT * FROM products WHERE id = %s", (product_id,)).fetchone()
        if p:
            items.append({"product_id": p["id"], "title": p["title"],
                          "price_rub": float(p["price_rub"]), "qty": qty})
    if consent not in ("on", "1", "true"):
        return JSONResponse({"ok": False, "error": "Нужно согласие на обработку данных"}, status_code=400)
    order = shop.create_order(user["id"], name, phone, email, comment, items)
    shop.log_message("site", "in", f"Новый заказ {order['number']} от {name}, {phone}. {comment}".strip(),
                     author=name)
    return JSONResponse({"ok": True, "number": order["number"], "id": order["id"]})


# ---------- Библиотека фото ----------

@router.post("/api/project/create")
def api_project_create(request: Request, title: str = Form(...)):
    """Создать папку-проект клиенту."""
    user = db.session_user(request.cookies.get("sid", ""))
    if not user:
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    pid = shop.create_project(user["id"], title.strip()[:120])
    return JSONResponse({"ok": True, "id": pid})


@router.post("/api/project/{project_id}/upload")
async def api_upload(project_id: int, request: Request, file: UploadFile = File(...)):
    """Загрузить фото в проект. Клиент может только в свой проект."""
    user = db.session_user(request.cookies.get("sid", ""))
    if not user:
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    with db.pool.connection() as conn:
        own = conn.execute("SELECT id FROM projects WHERE id = %s AND user_id = %s",
                           (project_id, user["id"])).fetchone()
    if not own:
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    content = await file.read()
    try:
        row = shop.save_upload(project_id, user["id"], file.filename or "photo.jpg", content)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=413)
    if not row:
        return JSONResponse({"ok": False, "error": "Разрешены только изображения"}, status_code=415)
    return JSONResponse({"ok": True, "id": row["id"], "name": row["orig_name"]})


@router.post("/api/order/{number}/photos")
async def api_order_photos(number: str, request: Request, files: list[UploadFile] = File(...)):
    """Фото к заказу. Только для владельца заказа или администратора.

    ВАЖНО: номер заказа НЕ является ключом доступа. Номера предсказуемы
    (AA-261123-001, 002, ...), поэтому раньше любой мог подобрать номер и
    загрузить файл в чужой заказ. Теперь нужна сессия и проверка владельца.
    """
    # Сначала сессия: без входа user = None, и обращение к user["id"] роняло обработчик с 500.
    user = db.session_user(request.cookies.get("sid", ""))
    if not user:
        return JSONResponse({"ok": False, "error": "Нужно войти", "login": True}, status_code=401)
    with db.pool.connection() as conn:
        order = conn.execute("SELECT * FROM orders WHERE number = %s", (number.strip(),)).fetchone()
    if not order:
        return JSONResponse({"ok": False, "error": "Заказ не найден"}, status_code=404)
    # Только владелец заказа или админ.
    if order.get("user_id") != user["id"] and user.get("role") != "admin":
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    if not files:
        return JSONResponse({"ok": False, "error": "Нет файлов"}, status_code=400)
    project_id = shop.project_for_order(order)
    saved, skipped = [], []
    for f in files:
        if len(saved) >= 20:
            break
        content = await f.read()
        try:
            row = shop.save_upload(project_id, order.get("user_id"), f.filename or "photo.jpg", content)
        except ValueError as e:
            skipped.append((f.filename, str(e)))
            continue
        if row:
            saved.append(row["orig_name"])
        else:
            skipped.append((f.filename, "не изображение"))
    # Событие в ленту: покупатель видит, что фото дошли.
    if saved:
        with db.pool.connection() as conn:
            add_event(conn, order["id"], "photos", body=f"Добавлено фото: {len(saved)}")
    return JSONResponse({"ok": True, "saved": len(saved), "skipped": len(skipped), "project_id": project_id})


@router.get("/api/file/{file_id}")
@router.get("/cabinet/file/{file_id}")
def serve_file(file_id: int, request: Request):
    """Отдать файл. Только владельцу или админу — прямые ссылки не работают."""
    user = db.session_user(request.cookies.get("sid", ""))
    if not user:
        return RedirectResponse("/login", status_code=303)
    with db.pool.connection() as conn:
        row = conn.execute("SELECT * FROM project_files WHERE id = %s", (file_id,)).fetchone()
    if not row:
        return denied(request, "Файл не найден", 404)
    if row["user_id"] != user["id"] and user["role"] != "admin":
        return denied(request)
    path = shop.file_path(row)
    if not os.path.exists(path):
        return denied(request, "Файл недоступен", 404)
    return FileResponse(path, filename=row["orig_name"])


# ---------- Единый инбокс ----------


def inbox_page(request: Request, admin_url: str) -> HTMLResponse:
    """Единое окно: все диалоги со всех каналов."""
    user = require_admin(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return render(request, "admin_inbox.html", user=user, threads=shop.threads())


@router.get("/api/inbox/threads")
def api_threads(request: Request):
    """Список диалогов. ТОЛЬКО администратор: здесь переписка реальных клиентов."""
    if not require_admin(request):
        return denied(request)
    return JSONResponse([
        {**t, "last_at": t["last_at"].isoformat() if t.get("last_at") else None,
         "created_at": t["created_at"].isoformat() if t.get("created_at") else None}
        for t in shop.threads()
    ])


@router.get("/api/inbox/thread/{thread_id}")
def api_thread(thread_id: int, request: Request):
    """Сообщения диалога. ТОЛЬКО администратор."""
    if not require_admin(request):
        return denied(request)
    msgs = shop.thread_messages(thread_id)
    return JSONResponse([
        {**m, "created_at": m["created_at"].isoformat() if m.get("created_at") else None}
        for m in msgs
    ])


@router.post("/api/inbox/thread/{thread_id}/reply")
def api_reply(thread_id: int, request: Request, body: str = Form(...)):
    """Ответ клиенту из единого окна — уходит в его канал."""
    user = require_admin(request)
    if not user:
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    with db.pool.connection() as conn:
        t = conn.execute("SELECT * FROM message_threads WHERE id = %s", (thread_id,)).fetchone()
    if not t:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    shop.log_message(t["channel"], "out", body, author="admin", thread_id=thread_id)
    import messaging
    sent = messaging.deliver(t, body)
    return JSONResponse({"ok": True, "sent": sent})
