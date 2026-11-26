"""Граница модуля со слоем «базы сайта».

Модуль НЕ импортирует базу сайта напрямую: он просит соединение у того, кто поставил модуль.
Сайт при старте вызывает connect(db) — и получает ровно те функции, которые нужны модулю:
    run(sql, args) -> список строк-словарей;  one(...) -> одна строка;  tx(...) -> изменить.
Так на другом сайте меняется одна строка подключения, а не тридцать запросов.
"""
from __future__ import annotations

_impl = None


def connect(impl) -> None:
    """Подключить модуль к базе сайта. impl — объекта с методами run/one/value/tx."""
    global _impl
    for name in ("run", "one", "value", "tx"):
        if not hasattr(impl, name):
            raise TypeError("в адаптере базы нет функции %s" % name)
    _impl = impl


def _need():
    """Проверка: модуль без подключения молчит непонятно — лучше сказать прямо."""
    if _impl is None:
        raise RuntimeError("content_factory: база не подключена (вызовите content_factory.connect(db))")
    return _impl


def run(sql: str, args=()) -> list[dict]:
    """Выполнить запрос и вернуть строки словарями. Для INSERT/UPDATE/DELETE — пустой список."""
    return _need().run(sql, args)


def one(sql: str, args=()):
    """Первая строка или None — для «найти по id» и «прочитать настройку»."""
    return _need().one(sql, args)


def value(sql: str, args=(), default=None):
    """Одно значение из первой колонки первой строки."""
    return _need().value(sql, args, default)


def tx(sql: str, args=()) -> None:
    """Выполнить один изменяющий запрос и зафиксировать."""
    _need().tx(sql, args)
