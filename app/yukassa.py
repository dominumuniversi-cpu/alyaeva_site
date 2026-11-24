"""ЮKassa: создание платежа и приём уведомления о поступлении денег.

Живёт отдельно от payments.py: тот отвечает на вопрос «есть ли оплата на сайте»,
а здесь — сама работа с деньгами.

Правила, которые важнее удобства:

1. Сумму берём ТОЛЬКО из нашей базы (orders.total_rub), а не из присланного запроса.
2. Заказ отмечаем оплаченным, только если ЮKassa подтвердила статус и сумма совпала.
3. Повторная доставка уведомления не создаёт второго платежа: ищем по id платежа.
4. Секретный ключ наружу не уходит ни в ответ, ни в лог.
"""
from __future__ import annotations

import json
import logging
import uuid

import httpx

import db

log = logging.getLogger("alyaeva.yukassa")

API = "https://api.yukassa.ru/v3/payments"

# Статусы ЮKassa, при которых деньги действительно пришли.
PAID_STATUSES = ("succeeded",)


def _shop() -> tuple[str, str]:
    """shopId и секретный ключ из настроек (ключ расшифровывается)."""
    import payments
    return payments._value("pay.yukassa.shop_id"), payments._value("pay.yukassa.secret")


def enabled() -> bool:
    """Оплата картой настроена: ключи есть и блок включён."""
    import payments
    return "yukassa" in payments.ready()


def create_payment(order: dict) -> dict:
    """Создать платёж по заказу. Возвращает {ok, url|error}."""
    shop_id, secret = _shop()
    if not shop_id or not secret:
        return {"ok": False, "error": "ЮKassa не настроена"}
    amount = float(order.get("total_rub") or 0)
    if amount <= 0:
        return {"ok": False, "error": "В заказе нет суммы"}
    body = {
        "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
        # Уникальный ключ: повторный вызов не создаст второй платёж.
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": "https://alyaeva.pro/cabinet",
        },
        "description": f"Именная книга, заказ {order.get('number') or order.get('id')}",
        "metadata": {"order_id": str(order.get("id"))},
    }
    try:
        resp = httpx.post(
            API,
            json=body,
            auth=(shop_id, secret),
            headers={"Idempotence-Key": str(uuid.uuid4())},
            timeout=20,
        )
    except httpx.HTTPError as e:
        # В лог — класс ошибки, без ключа и без тела с реквизитами.
        log.warning("ЮKassa недоступна: %s", type(e).__name__)
        return {"ok": False, "error": "Платёжный сервис недоступен"}
    if resp.status_code not in (200, 201):
        log.warning("ЮKassa ответила %s", resp.status_code)
        return {"ok": False, "error": "Платёжный сервис отклонил запрос"}
    data = resp.json()
    url = ((data.get("confirmation") or {}).get("confirmation_url")) or ""
    if not url:
        return {"ok": False, "error": "Платёжный сервис не вернул ссылку"}
    _remember(order.get("id"), data.get("id", ""), "pending", amount)
    return {"ok": True, "url": url, "payment_id": data.get("id", "")}


def _remember(order_id, payment_id: str, status: str, amount: float) -> None:
    """Записать платёж в историю заказа (order_events): видно, что и когда попросили оплатить."""
    with db.pool.connection() as conn:
        conn.execute(
            "INSERT INTO order_events (order_id, kind, status, body) VALUES (%s, 'payment', %s, %s)",
            (order_id, status, f"платёж {payment_id}: {amount:.2f} ₽"),
        )
        conn.commit()


def handle_notification(payload: dict) -> tuple[int, str]:
    """Уведомление от ЮKassa. Возвращает код ответа и текст — ЮKassa ждёт 200."""
    event = payload.get("event", "")
    obj = payload.get("object") or {}
    status = obj.get("status", "")
    payment_id = obj.get("id", "")
    # Присланный order_id — данные извне: может быть строкой «42», а может быть мусором.
    raw_order = (obj.get("metadata") or {}).get("order_id")
    try:
        order_id = int(str(raw_order).strip())
    except (TypeError, ValueError):
        log.warning("уведомление с нечисловым order_id: %r", raw_order)
        return 200, "ignored"
    # Сумма из уведомления — только для сверки с нашей.
    try:
        paid = float(((obj.get("amount") or {}).get("value")) or 0)
    except (TypeError, ValueError):
        paid = 0.0

    if not order_id:
        log.warning("уведомление без order_id: %s", payment_id)
        return 200, "ignored"

    with db.pool.connection() as conn:
        order = conn.execute("SELECT id, total_rub FROM orders WHERE id = %s", (order_id,)).fetchone()
    if not order:
        log.warning("уведомление по неизвестному заказу: %s", order_id)
        return 200, "ignored"

    expected = float(order["total_rub"] or 0)
    if event != "payment.succeeded" or status not in PAID_STATUSES:
        _remember(order["id"], payment_id, status or event, paid)
        return 200, "accepted"

    # Деньги пришли: сверяем сумму. Не сошлась — не отмечаем, но и не теряем след.
    if round(paid, 2) != round(expected, 2):
        log.warning("сумма не совпала: заказ %s ожидали %.2f, пришло %.2f", order_id, expected, paid)
        _remember(order["id"], payment_id, "amount_mismatch", paid)
        return 200, "mismatch"

    with db.pool.connection() as conn:
        # Статусы в базе — коды ('paid'), не русские подписи: CHECK не пропустит слово (#122).
        conn.execute(
            "UPDATE orders SET status = 'paid', updated_at = now() WHERE id = %s", (order["id"],)
        )
        conn.execute(
            "INSERT INTO order_events (order_id, kind, status, body) VALUES (%s, 'payment', 'paid', %s)",
            (order["id"], f"оплачен {paid:.2f} ₽, платёж {payment_id}"),
        )
        conn.commit()
    return 200, "ok"


def parse(raw: bytes) -> dict:
    """Разобрать тело уведомления. Мусор — пустой словарь, а не исключение."""
    try:
        data = json.loads(raw or b"{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}
