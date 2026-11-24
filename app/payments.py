"""Оплата: три способа, один экран настроек, один ответ на вопрос «есть ли оплата на сайте».

Владелец (24.11.2026): «Добавь в админку возможность интеграции с ЮKassa… Дополнительно добавь
возможности 1 и 2. Делай разными блоками в настройках админки, но в одном меню настроек оплаты.
Каждый блок может включаться и выключаться чекбоксом. Если все блоки выключены, возможности
онлайн оплаты на сайте нет. Если хотя бы один из блоков включён и настроен, показывать оплату».

Три блока:

| Ключ | Что это | Без чего не готов |
|---|---|---|
| bank | счёт в банке для самозанятых: реквизиты и инструкция | получатель + номер счёта |
| link | ссылка на оплату от банка (человек создаёт её в приложении банка) | адрес ссылки |
| yukassa | оплата картой через ЮKassa | shop_id + секретный ключ |

Правило показа одно: `ready()` возвращает включённые И заполненные блоки.
Пусто — значит на сайте нет ни страницы оплаты, ни ссылок на неё.
"""
from __future__ import annotations

import secrets as _secrets

import db
import secrets_helper

# Порядок блоков — как на странице настроек и на странице /oplata.
BLOCKS = ("bank", "link", "yukassa")

# Что нужно, чтобы блок считался рабочим. Секретные поля проверяются как непустые.
REQUIRED = {
    "bank": ("pay.bank.recipient", "pay.bank.account"),
    "link": ("pay.link.url",),
    "yukassa": ("pay.yukassa.shop_id", "pay.yukassa.secret"),
}

# Поля блока, которые хранятся зашифрованными: токены и ключи.
SECRET_FIELDS = {"pay.yukassa.secret"}

TITLES = {
    "bank": "Счёт в банке",
    "link": "Ссылка на оплату",
    "yukassa": "ЮKassa",
}


def _value(key: str) -> str:
    """Значение настройки. Секреты расшифровываем: наружу отдаём только через mask()."""
    raw = str(db.get_setting(key, "") or "")
    if key in SECRET_FIELDS and raw:
        return secrets_helper.decrypt(raw)
    return raw.strip()


def is_on(block: str) -> bool:
    """Блок включён чекбоксом (не значит «настроен»)."""
    return str(db.get_setting(f"pay.{block}.on", "") or "").strip() in ("1", "on", "true", "да")


def missing(block: str) -> list[str]:
    """Каких обязательных полей не хватает — по-человечески, для админки."""
    names = {
        "pay.bank.recipient": "получатель",
        "pay.bank.account": "номер счёта",
        "pay.link.url": "ссылка на оплату",
        "pay.yukassa.shop_id": "shopId",
        "pay.yukassa.secret": "секретный ключ",
    }
    return [names.get(k, k) for k in REQUIRED.get(block, ()) if not _value(k)]


def state() -> dict:
    """Что настроено: по каждому блоку — включён, готов, чего не хватает."""
    result = {}
    for block in BLOCKS:
        gaps = missing(block)
        result[block] = {
            "title": TITLES[block],
            "on": is_on(block),
            "ready": is_on(block) and not gaps,
            "missing": gaps,
        }
    return result


def ready() -> list[str]:
    """Готовые блоки в порядке показа. Пустой список — оплаты на сайте нет."""
    st = state()
    return [b for b in BLOCKS if st[b]["ready"]]


def available() -> bool:
    """Есть ли на сайте оплата вообще. Один ответ для всех шаблонов."""
    return bool(ready())


def details(block: str) -> dict:
    """Поля блока для страницы /oplata. Секреты сюда не попадают никогда."""
    if block == "bank":
        return {
            "recipient": _value("pay.bank.recipient"),
            "account": _value("pay.bank.account"),
            "bank_name": _value("pay.bank.bank_name"),
            "sbp_phone": _value("pay.bank.sbp_phone"),
            "note": _value("pay.bank.note"),
        }
    if block == "link":
        return {"url": _value("pay.link.url"), "note": _value("pay.link.note")}
    return {"note": _value("pay.yukassa.note")}


def webhook_url() -> str:
    """Адрес уведомлений ЮKassa: без отдельного секрета, проверяем по подписи и сумме."""
    site = str(db.get_setting("site.url", "") or "https://alyaeva.pro").rstrip("/")
    return site + "/hooks/yukassa"


def new_label() -> str:
    """Метка для уведомлений: по ней находим заказ, не доверяя присланному телу."""
    return _secrets.token_urlsafe(12)
