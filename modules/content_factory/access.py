"""Аксессор постов для модуля: одна таблица, один интерфейс, арендатор в ключе.

Почему не своя таблица: у сайта уже есть её собственная таблица постов, и вторая «почти
такая же» раздвоила бы историю. Модуль берёт существующую, но обращается к ней только через
этот файл — перенос на другой сайт = замена этого файла (или таблица posts в её схеме).

Арендатор (tenant): в одном сайте он пустой; если модуль поставлен несколькими копиями
на одну базу, в ключе появляется имя — и посты двух сайтов не смешиваются.

Разбор «один пост — много площадок»:
- posts — материал и поля поста;
- cf_variants — готовый текст для каждой площадки (правится человеком по отдельности);
- posts.channels — какие площадки у поста включены;
- posts.result — что ответила каждая площадка (JSON).
"""
from __future__ import annotations

import json

from . import dbsession, platforms

#: Что показывать человеку вместо внутреннего статуса.
STATUSES = {
    "draft": "Черновик",
    "queued": "В очереди",
    "sending": "Отправляется",
    "sent": "Опубликован",
    "partial": "Ушёл частично",
    "failed": "Не ушёл никуда",
}

#: Статусы, при которых пост ещё можно править.
EDITABLE = {"draft", "queued", "failed", "partial"}


def tenant() -> str:
    """Имя арендатора из настроек сайта. Пусто — значит сайт один."""
    from . import settings
    return str(settings.get("cf.tenant", "") or "").strip()


def _split(text) -> list[str]:
    """Строка 'telegram,vk' → список площадок. Мусор и неизвестные площадки отбрасываем."""
    got = [x.strip() for x in str(text or "").split(",")]
    return [x for x in got if platforms.get(x)]


def _row(d: dict) -> dict:
    """Строка таблицы → словарь для страницы: площадки списком, статус и ответы разобраны."""
    d["channels_list"] = _split(d.get("channels"))
    d["platforms"] = d["channels_list"]
    d["status_name"] = STATUSES.get(d.get("status"), d.get("status") or "")
    try:
        d["result_map"] = json.loads(d.get("result") or "{}")
    except (TypeError, ValueError):
        d["result_map"] = {}
    return d


def list_posts(limit: int = 60) -> list[dict]:
    """Посты свежие сверху — лента на странице. Ответы каналов разобраны словарями."""
    t = tenant()
    rows = dbsession.run(
        """SELECT p.*, COALESCE(p.title, '') AS title, COALESCE(p.seo_slug, '') AS slug
             FROM posts p
            WHERE COALESCE(p.tenant, '') = %s
            ORDER BY COALESCE(p.publish_at, p.created_at) DESC, p.id DESC
            LIMIT %s""",
        (t, limit),
    )
    return [_row(r) for r in rows]


def get(post_id: int) -> dict | None:
    """Один пост по id, с вариантами по площадкам."""
    row = dbsession.one("SELECT * FROM posts WHERE id = %s", (post_id,))
    if not row:
        return None
    d = _row(dict(row))
    variants = dbsession.run("SELECT platform, text, edited_by_human FROM cf_variants WHERE post_id = %s",
                             (post_id,))
    d["variants"] = {v["platform"]: v for v in variants}
    return d


def create(body: str, title: str, channels: list[str], media: str,
           created_by: int | None, variants: dict | None = None, publish_at=None,
           seo_description: str = "") -> int:
    """Новый пост. Площадок нет — ошибка, а не «выберем Telegram на всякий случай».

    Молча выбранный канал означает, что человек однажды не поймёт, откуда взялся пост,
    — этот урок уже был при первом заходе на страницу постов (24.11.2026).
    """
    chans = ",".join(p for p in platforms.keys() if p in set(channels))
    if not chans:
        raise ValueError("Не выбрана ни одна площадка")
    t = tenant()
    row = dbsession.one(
        """INSERT INTO posts (title, body, media_url, channels, status, tenant, created_by)
           VALUES (%s, %s, %s, %s, 'draft', %s, %s) RETURNING id""",
        (title or "", body, media or "", chans, t, created_by),
    )
    post_id = int(row["id"])
    save_variants(post_id, variants or {})
    if publish_at is not None or seo_description:
        update(post_id, publish_at=publish_at, seo_description=seo_description)
    return post_id


def save_variants(post_id: int, variants: dict) -> None:
    """Записать тексты по площадкам. Только известные площадки, только до пустой строки."""
    for key, v in (variants or {}).items():
        if not platforms.get(key):
            continue
        text = v.get("text") if isinstance(v, dict) else str(v or "")
        edited = bool(v.get("edited_by_human")) if isinstance(v, dict) else False
        dbsession.tx(
            """INSERT INTO cf_variants (post_id, platform, text, edited_by_human)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (post_id, platform) DO UPDATE SET text = EXCLUDED.text,
                 edited_by_human = EXCLUDED.edited_by_human""",
            (post_id, key, text or "", edited),
        )


def update(post_id: int, body=None, title=None, channels=None, media=None, publish_at=None,
           slug=None, seo_description=None, status=None) -> None:
    """Правка полей поста. None — «не трогать»: так форма не стирает неотправленные поля.

    Это правило уже спасало настройки сайта: пустое поле формы не должно обнулять
    то, чего человек не касался. Здесь то же самое, только на уровне запроса.
    """
    sets, args = [], []
    if body is not None:
        sets.append("body = %s"); args.append(body)
    if title is not None:
        sets.append("title = %s"); args.append(title)
    if media is not None:
        sets.append("media_url = %s"); args.append(media)
    if channels is not None:
        chans = ",".join(p for p in platforms.keys() if p in set(channels))
        sets.append("channels = %s"); args.append(chans)
    if publish_at is not None:
        sets.append("publish_at = %s"); args.append(publish_at or None)
    if slug is not None:
        sets.append("seo_slug = %s"); args.append(slug)
    if seo_description is not None:
        sets.append("seo_description = %s"); args.append(seo_description)
    if status is not None:
        sets.append("status = %s"); args.append(status)
    if not sets:
        return
    args.append(post_id)
    dbsession.tx("UPDATE posts SET %s WHERE id = %%s" % ", ".join(sets), tuple(args))


def delete(post_id: int) -> bool:
    """Удалить черновик и его варианты. Опубликованное не трогаем — это история."""
    row = dbsession.one("SELECT status FROM posts WHERE id = %s", (post_id,))
    if not row:
        return False
    if row["status"] in ("sent", "queued", "sending"):
        return False
    dbsession.tx("DELETE FROM cf_variants WHERE post_id = %s", (post_id,))
    dbsession.tx("DELETE FROM posts WHERE id = %s", (post_id,))
    return True


def mark_result(post_id: int, status: str, result: dict) -> None:
    """Что ответила каждая площадка. Пишем даже при отказе: без этого причина потери не видна."""
    dbsession.tx(
        """UPDATE posts SET status = %s, result = %s,
             published_at = CASE WHEN %s IN ('sent', 'partial') THEN now() ELSE published_at END
           WHERE id = %s""",
        (status, json.dumps(result, ensure_ascii=False), status, post_id),
    )


def due(limit: int = 10) -> list[dict]:
    """Посты, которым пора уходить: стоят в очереди и время пришло."""
    return [_row(r) for r in dbsession.run(
        """SELECT * FROM posts
            WHERE status = 'queued' AND publish_at IS NOT NULL AND publish_at <= now()
            ORDER BY publish_at LIMIT %s""", (limit,))]


def claim(post_id: int) -> bool:
    """Занять пост перед отправкой, чтобы два процесса не отправили его дважды.

    UPDATE ... WHERE status IN (...) возвращает строку, только если пост ещё не занят:
    это атомарная проверка в самой базе, а не «прочитал и проверил» в коде.
    """
    rows = dbsession.run(
        "UPDATE posts SET status = 'sending' WHERE id = %s AND status IN ('queued', 'draft') RETURNING id",
        (post_id,),
    )
    return bool(rows)
