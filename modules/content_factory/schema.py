"""Миграция модуля: что он создаёт в базе.

Правило то же, что у сайта: только добавляющее. Ни одного DROP, ни одного изменения
существующих колонок — модуль обязан устанавливаться поверх работающего сайта и не
трогать чужие таблицы.

Таблицы модуля:
- cf_variants   — тексты поста для площадок (пост = материал + варианты);
- cf_targets    — площадки (адрес, id аккаунта) и что подключено;
- cf_fingerprints — отпечатки уже опубликованного (чтобы не запостить дважды);
- cf_generations — история автогенерации текста (что просили, что ответили, сколько стоило);
- cf_llm        — подключение нейросетей (когда кнопка «Сочинить» перестаёт быть серой).

Рабочие тексты постов живут в существующей таблице posts (модуль подключается к ней
через свой аксессор — см. access.py): у сайта уже есть такая, и заводить вторую
«почти такую же» значит раздвоить историю. Колонки, которых у неё нет (title, tenant,
seo_description, seo_slug), модуль добавляет сам — ADD COLUMN IF NOT EXISTS, безопасно
для работающего сайта.
"""
from __future__ import annotations

from . import dbsession

VERSION = "011_content_factory"

STATEMENTS = [
    # Площадки модуля: что подключено и куда публиковать.
    # Колонки, которых нет у таблицы постов сайта: заголовок, арендатор (для нескольких сайтов
    # на одной базе), краткое описание и ссылка для страницы «статья» на самом сайте.
    "ALTER TABLE posts ADD COLUMN IF NOT EXISTS title TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE posts ADD COLUMN IF NOT EXISTS tenant TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE posts ADD COLUMN IF NOT EXISTS seo_description TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE posts ADD COLUMN IF NOT EXISTS seo_slug TEXT NOT NULL DEFAULT ''",
    # Площадки модуля: что подключено и куда публиковать.
    """CREATE TABLE IF NOT EXISTS cf_variants (
        id            BIGSERIAL PRIMARY KEY,
        post_id       BIGINT NOT NULL,
        platform      TEXT NOT NULL,
        text          TEXT NOT NULL DEFAULT '',
        edited_by_human BOOLEAN NOT NULL DEFAULT false,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (post_id, platform)
    )""",
    """CREATE TABLE IF NOT EXISTS cf_targets (
        platform      TEXT PRIMARY KEY,
        enabled       BOOLEAN NOT NULL DEFAULT false,
        address       TEXT NOT NULL DEFAULT '',
        label         TEXT NOT NULL DEFAULT '',
        updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    # Отпечатки: «этот текст уже ушёл в эту площадку». Защита от двойной публикации
    # при повторном нажатии и при публикации из расписания после ручной отправки.
    """CREATE TABLE IF NOT EXISTS cf_fingerprints (
        hash          TEXT PRIMARY KEY,
        platform      TEXT NOT NULL,
        post_id       BIGINT,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS cf_generations (
        id            BIGSERIAL PRIMARY KEY,
        post_id       BIGINT,
        provider      TEXT NOT NULL DEFAULT '',
        model         TEXT NOT NULL DEFAULT '',
        brief         TEXT NOT NULL DEFAULT '',
        answer        TEXT NOT NULL DEFAULT '',
        ok            BOOLEAN NOT NULL DEFAULT false,
        detail        TEXT NOT NULL DEFAULT '',
        ms            INTEGER NOT NULL DEFAULT 0,
        created_by    BIGINT,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS cf_llm (
        id            BIGSERIAL PRIMARY KEY,
        provider      TEXT NOT NULL,
        label         TEXT NOT NULL DEFAULT '',
        base_url      TEXT NOT NULL DEFAULT '',
        model         TEXT NOT NULL DEFAULT '',
        token         TEXT NOT NULL DEFAULT '',
        enabled       BOOLEAN NOT NULL DEFAULT false,
        updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (provider)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_cf_variants_post ON cf_variants(post_id)",
    "CREATE INDEX IF NOT EXISTS idx_cf_gen_created ON cf_generations(created_at DESC)",
]


def install() -> list[str]:
    """Создать недостающее. Возвращает пустой список при успехе — им проверяют установку."""
    from . import llm

    problems: list[str] = []
    for sql in STATEMENTS:
        try:
            dbsession.run(sql)
        except Exception as e:  # noqa: BLE001 — установка обязана сказать, что именно не создалось
            problems.append("%s: %s" % (sql.split("(")[0].strip()[:60], e))
    try:
        llm.ensure_builtin()
    except Exception as e:  # noqa: BLE001
        problems.append("cf_llm: %s" % e)
    return problems
