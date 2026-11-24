"""Статусы заказов — единственный источник правды.

Раньше словарей было ДВА: один в routes_shop с "shipped", другой в admin_clients с "sent".
Покупатель видел один набор, админка предлагала другой, и статус "Отправлен" в админке
не совпадал с тем, что знает кабинет. Теперь словарь здесь, все импортируют отсюда.
"""
from __future__ import annotations

STATUS_NAMES = {
    "new": "Новый",
    "in_work": "В работе",
    "layout": "На согласовании макета",
    "paid": "Оплачен",
    "printing": "В печати",
    "shipped": "Отправлен",
    "done": "Выполнен",
    "cancelled": "Отменён",
}

# Порядок этапов для шкалы прогресса. Отменён в шкалу не входит — он вне пути.
STATUS_ORDER = ["new", "in_work", "layout", "paid", "printing", "shipped", "done"]

# Человеческое название этапа для шкалы (короче, чем полный статус).
STAGE_NAMES = {
    "new": "Заявка",
    "in_work": "В работе",
    "layout": "Макет",
    "paid": "Оплата",
    "printing": "Печать",
    "shipped": "Доставка",
    "done": "Готово",
}


def status_name(code: str) -> str:
    """Понятное клиенту название статуса заказа."""
    return STATUS_NAMES.get(code or "new", "Новый")


def stage_index(code: str) -> int:
    """Номер этапа на шкале прогресса. Отменённый заказ показывает -1."""
    return STATUS_ORDER.index(code) if code in STATUS_ORDER else -1
