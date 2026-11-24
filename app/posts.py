"""Постинг в каналы: собирает пост и отдаёт каналу. Токены не нужны — здесь только сборка.

Таблица `posts` уже есть (тело, фото, каналы, статус, время). Завод делится на две части:
провод — этот модуль, он работает без ключей; отправка — `messaging.send_telegram` /
`send_vk`, они ждут токены из задачи #26. Пока токенов нет, посты собираются в статусе `draft`.
"""

import logging
import re

import pricing

log = logging.getLogger("alyaeva.posts")

MAX_TELEGRAM = 1024
MAX_VK = 4000
SITE = "https://alyaeva.pro"


def limit_for(channel: str) -> int:
    """Сколько символов влезает в один пост канала."""
    return MAX_VK if channel == "vk" else MAX_TELEGRAM


def _format_name(kind: str) -> str:
    """Название формата по ключу из `pricing.FORMATS`; пусто, если ключа нет."""
    row = next((x for x in pricing.FORMATS if x["key"] == kind), None)
    return row["name"] if row else ""


def _price_line(kind: str) -> str:
    """Строка про цену — из `pricing`, того же места, откуда считает страница заказа."""
    if not kind:
        return ""
    row = next((x for x in pricing.FORMATS if x["key"] == kind), None)
    if not row:
        return ""
    base = int(row.get("base") or 0)
    money = f"{base:,}".replace(",", "\u00a0")
    return f"{row['name']} — {money} ₽" if base else f"{row['name']} — по запросу"


def cut(text: str, limit: int) -> str:
    """Обрезать по последнему пробелу, чтобы не рвать слово на полуслове."""
    if len(text) <= limit:
        return text
    head = text[: limit - 1]
    space = head.rfind(" ")
    return (head[:space] if space > limit // 2 else head).rstrip() + "…"


def compose(template: str, channel: str, topic: str = "", kind: str = "") -> str:
    """Собрать текст поста: текст, тема, цена формата и ссылка на заказ."""
    parts = [str(template or "").strip()]
    if topic:
        parts.append(str(topic).strip())
    line = _price_line(kind)
    if line:
        parts.append(line)
    parts.append(f"Заказать: {SITE}/order")
    return cut("\n\n".join(x for x in parts if x), limit_for(channel))


def channels_ready(settings: dict) -> list:
    """Какие каналы можно отправлять сейчас: есть токен и куда публиковать."""
    ready = []
    if str(settings.get("telegram.token") or "").strip() and str(settings.get("telegram.channel") or "").strip():
        ready.append("telegram")
    if str(settings.get("vk.token") or "").strip() and str(settings.get("vk.group") or "").strip():
        ready.append("vk")
    return ready


def html_to_plain(text: str) -> str:
    """Telegram понимает HTML, ВКонтакте — нет: перед публикацией во ВК теги убираем."""
    return re.sub(r"<[^>]+>", "", str(text or ""))
