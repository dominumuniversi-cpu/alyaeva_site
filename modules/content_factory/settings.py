"""Настройки сайта для модуля: ключ → значение.

У сайта настройки лежат в app_settings JSON-значениями и пишутся журналом аудита.
Модуль пользуется тем же хранилищем: одна база, одна история изменений, ни одной
отдельной таблицы настроек, которую кто-то забудет забэкапить.
"""
from __future__ import annotations

import json

from . import dbsession


def get(key: str, default=None):
    """Прочитать настройку по ключу-пути."""
    row = dbsession.one("SELECT value FROM app_settings WHERE key = %s", (key,))
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return row["value"]


def set(key: str, value, user_id: int | None = None) -> None:
    """Записать настройку и след изменения в журнал аудита — так же, как остальная админка.

    Журнал пишем прямо здесь, а не через db.set_setting: модуль работает со своим мостом,
    а формат app_settings_audit тот же, поэтому история изменений у сайта одна.
    """
    raw = json.dumps(value, ensure_ascii=False)
    old = dbsession.one("SELECT value FROM app_settings WHERE key = %s", (key,))
    dbsession.tx(
        """INSERT INTO app_settings (key, value, updated_at, updated_by)
           VALUES (%s, %s, now(), %s)
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value,
             updated_at = now(), updated_by = EXCLUDED.updated_by""",
        (key, raw, user_id),
    )
    dbsession.tx(
        "INSERT INTO app_settings_audit (key, old_value, new_value, changed_by) VALUES (%s,%s,%s,%s)",
        (key, old["value"] if old else None, raw, user_id),
    )


def prefix(p: str) -> dict:
    """Все настройки с префиксом — для страницы «Цели» и проверки готовности площадок."""
    rows = dbsession.run("SELECT key, value FROM app_settings WHERE key LIKE %s", (p + "%",))
    out = {}
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value"])
        except (TypeError, ValueError):
            out[r["key"]] = r["value"]
    return out
