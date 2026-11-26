"""Сборка контент-завода: что нужно сайту, чтобы поставить модуль.

Сайт делает две вещи:
    cf.connect(db)          # адаптер базы сайта: run / one / value / tx
    cf.install()            # таблицы + фоновый исполнитель

Всё остальное модуль делает сам: страница — /admin/posts (роутер modules/content_factory/routes.py).
Модуль НЕ импортирует ни одного файла сайта: база приходит адаптером, секреты — шифровальщиком
(secrets_helper), проверка администратора — хуком. Список хуков и что заменить — README.md рядом.
"""
from __future__ import annotations

from . import (adapt, access, channels, crypto, dbsession, generator, llm, platforms, queue,  # noqa: F401
               schema, settings, worker)

__all__ = ["connect", "secure", "install", "adapt", "access", "channels", "generator", "llm",
           "platforms", "queue", "schema", "settings", "worker"]


def connect(impl) -> None:
    """Подключить базу сайта: модуль работает через переданный адаптер, а не через свой psycopg."""
    dbsession.connect(impl)


def secure(encrypt, decrypt) -> None:
    """Подключить шифрование секретов сайта: токены площадок и ключи нейросетей лягут шифром.

    Не подключили — модуль честно скажет это при первой попытке сохранить токен,
    а не сохранит секрет открытым текстом «на всякий случай».
    """
    crypto.connect(encrypt, decrypt)


def install() -> list[str]:
    """Установить завод: создать таблицы и поднять фоновый исполнитель.

    Возвращает список проблем — пустой значит «поставился». Сайт решает, показать ли их:
    молча упавшая установка даёт пустую страницу, и причина «почему» не видна никому.
    """
    problems = schema.install()
    try:
        worker.start()
    except Exception as e:  # noqa: BLE001 — фон не должен мешать сайту подняться
        problems.append("фоновый исполнитель: %s" % e)
    return problems
