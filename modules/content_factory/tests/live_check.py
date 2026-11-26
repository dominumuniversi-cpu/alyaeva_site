"""Проверка завода на живой базе, без интерфейса и без отправки в каналы.

Запуск из контейнера:
    docker cp modules alyaeva-web:/tmp/cfcheck && \
    docker exec -w /tmp/cfcheck alyaeva-web python modules/content_factory/tests/live_check.py

Проверяет: установку схемы (можно повторно — установка обязана быть идемпотентной),
сохранение поста с вариантами, защиту от двойной публикации, расписание.
НЕ трогает настоящие токены и НЕ отправляет ничего в интернет: площадки выключены.
Всё созданное удаляется в конце — база остаётся как была.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
APP = "/app"
for p in (ROOT, APP):
    if p not in sys.path:
        sys.path.insert(0, p)

import db  # noqa: E402 — приложение сайта: доступ к базе
import secrets_helper  # noqa: E402 — шифрование секретов сайта
from modules import content_factory as cf  # noqa: E402


class SiteDb:
    """Адаптер базы (тот же, что в app/content_factory_glue.py) — здесь копией, чтобы проверка
    не тянула маршруты сайта."""

    def run(self, sql, args=()):
        with db.pool.connection() as conn:
            cur = conn.execute(sql, tuple(args))
            rows = cur.fetchall() if cur.description else []
            conn.commit()
        return [dict(r) for r in rows]

    def one(self, sql, args=()):
        rows = self.run(sql, args)
        return rows[0] if rows else None

    def value(self, sql, args=(), default=None):
        row = self.one(sql, args)
        return next(iter(row.values())) if row else default

    def tx(self, sql, args=()):
        with db.pool.connection() as conn:
            conn.execute(sql, tuple(args))
            conn.commit()


def main() -> int:
    """Прогон проверок. 0 — всё прошло."""
    os.environ["CF_WORKER"] = "0"  # исполнитель не нужен: он может опубликовать чужое расписание
    db.open_pool()
    cf.connect(SiteDb())
    cf.secure(secrets_helper.encrypt, secrets_helper.decrypt)
    bad = []

    p1 = cf.install()
    p2 = cf.install()
    if p1 or p2:
        bad.append("установка: %s / повторно: %s" % (p1, p2))
    else:
        print("ok   установка схемы (и повторная — идемпотентна)")

    keys = [r["key"] for r in db.all_settings("cf.targets.")]
    if any(k.endswith(".token") for k in keys):
        bad.append("в базе уже есть токены площадок — проверка не должна их трогать")

    texts = cf.adapt.adapt_all("Проверка завода: **жирный** и обычный текст. " * 40,
                               ["telegram", "vk", "instagram"], ["https://alyaeva.pro/order"],
                               {"instagram": "alyaeva"})
    if len(texts["telegram"]["text"]) > 1024 or "**" in texts["telegram"]["text"]:
        bad.append("telegram: лимит или разметка")
    else:
        print("ok   адаптация: TG %d/1024, VK %d/16000, IG %d/2200 (%s)" % (
            len(texts["telegram"]["text"]), len(texts["vk"]["text"]), len(texts["instagram"]["text"]),
            "обрезано" if texts["telegram"]["truncated"] else "влезло целиком"))

    post_id = cf.access.create("Проверочный материал завода", "Проверка",
                               ["telegram", "vk"], "", None,
                               {k: {"text": texts[k]["text"]} for k in ("telegram", "vk")}, None)
    got = cf.access.get(post_id)
    if not got or set(got.get("variants") or {}) != {"telegram", "vk"}:
        bad.append("сохранение поста: варианты не те: %s" % sorted((got or {}).get("variants") or {}))
    else:
        print("ok   пост сохранён с вариантами: %s" % sorted(got["variants"]))

    # Защита от двойной публикации: отпечаток — на текст конкретной площадки.
    cf.queue.remember("vk", "Текст для ВК", post_id)
    if not cf.queue.already_sent("vk", "Текст для ВК"):
        bad.append("отпечаток публикации не запомнился")
        
    elif cf.queue.already_sent("telegram", "Текст для ВК"):
        bad.append("отпечаток срабатывает на чужую площадку")
    else:
        print("ok   защита от двойной публикации: ВК помнит, Telegram — нет")

    when = datetime(2030, 1, 1, 9, 0)
    cf.queue.schedule(post_id, when)
    queued = cf.access.get(post_id)["status"]
    if queued != "queued":
        bad.append("расписание не поставило пост в очередь: %s" % queued)
    else:
        print("ok   расписание: пост в очереди на %s" % when)
    cf.queue.schedule(post_id, None)
    if cf.access.get(post_id)["status"] != "draft":
        bad.append("снятие с очереди не вернуло черновик")
    else:
        print("ok   снятие с очереди: снова черновик")

    if not cf.access.delete(post_id):
        bad.append("удаление черновика не сработало")
    else:
        print("ok   удаление черновика")
    # Убираем и отпечаток своей проверки: иначе в базе остаётся след от проверки,
    # а такие следы уже путали статистику (docs/mistakes.md, «мои же проверки оставляют следы»).
    SiteDb().tx("DELETE FROM cf_fingerprints WHERE post_id = %s", (post_id,))

    db.close_pool()
    if bad:
        for b in bad:
            print("FAIL", b)
        return 1
    print("завод проверен: всё в порядке")
    return 0


if __name__ == "__main__":
    sys.exit(main())
