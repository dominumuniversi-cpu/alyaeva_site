"""Сайт контент-завода, его роутер и подключение к базе сайта.

Сайт делает две вещи:
    при импорте:  content_factory_glue.wire()    # база, шифрование, хуки (роутер — в main.py)
    после пула:   content_factory_glue.start()   # схема + фоновый исполнитель (в lifespan)

Сама страница — modules/content_factory/routes.py: она про сайт не знает, ей передаются
три хука: кто администратор, чем отрисовать шаблон в оболочке сайта, как шифровать секреты.
"""
from __future__ import annotations

import logging

import db
import secrets_helper

from modules import content_factory

log = logging.getLogger("alyaeva.cf")


class SiteDb:
    """Адаптер базы сайта для модуля: один пул, ничего своего.

    Почему адаптер, а не «пусть модуль сам откроет соединение»: два пула к одной базе
    означают два места, где настраиваются креды, и два способа получить отказ подключения.
    """

    def run(self, sql, args=()):
        """Выполнить запрос, вернуть строки словарями."""
        with db.pool.connection() as conn:
            cur = conn.execute(sql, tuple(args))
            rows = cur.fetchall() if cur.description else []
            conn.commit()
        return [dict(r) for r in rows]

    def one(self, sql, args=()):
        """Первая строка или None."""
        rows = self.run(sql, args)
        return rows[0] if rows else None

    def value(self, sql, args=(), default=None):
        """Одно значение из первой колонки первой строки."""
        row = self.one(sql, args)
        return next(iter(row.values())) if row else default

    def tx(self, sql, args=()):
        """Изменить и зафиксировать."""
        with db.pool.connection() as conn:
            conn.execute(sql, tuple(args))
            conn.commit()


def wire() -> None:
    """Подключить модуль к сайту при импорте: база, шифрование, хуки прав и отрисовки.

    Только провода — ни одного запроса к базе. Запросы (схема, фоновый исполнитель) делаются
    позже, в start(): при импорте пул ещё закрыт, и установка молча падала
    «pool is not open yet» (поймано первым запуском образа).
    """
    content_factory.connect(SiteDb())
    content_factory.secure(secrets_helper.encrypt, secrets_helper.decrypt)
    # Хуки модуля → сайт: кто админ и чем рисовать страницу в оболочке админки сайта.
    from modules.content_factory import hooks
    import content_factory_ui as ui
    hooks.admin = ui.admin
    hooks.render = ui.render


def start() -> list[str]:
    """Включить завод после открытия базы: создать схему и поднять фоновый исполнитель.

    Возвращает проблемы — пусто значит «всё хорошо». Ошибки не бросаем: завод не должен
    мешать сайту подняться, но и молчать о том, что он не поставился, нельзя.
    """
    problems: list[str] = []
    try:
        problems = content_factory.install()
    except Exception as e:  # noqa: BLE001 — страница должна открыться, даже если таблиц нет
        log.warning("контент-завод не установился: %s", e)
        problems = ["установка: %s" % e]
    for p in problems:
        log.warning("контент-завод: %s", p)
    return problems
