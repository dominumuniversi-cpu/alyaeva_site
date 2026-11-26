"""Проверка модуля контент-завода без сайта, базы и сети.

Запуск: python modules/content_factory/tests/test_module.py
(или pytest, если он установлен). Проверяется то, что нельзя проверить глазами:
лимиты площадок, обрезка, разметка, подпись, и защита от «половины поста».
"""
from __future__ import annotations

import html
import os
import re
import sys

# Путь к корню репозитория: tests → content_factory → modules → корень.
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.content_factory import adapt, platforms  # noqa: E402


def test_limits_are_real() -> None:
    """У каждой площадки лимит положительный, и у Telegram он действительно 1024."""
    assert platforms.get("telegram").limit == 1024
    assert platforms.get("vk").limit >= 4000
    assert platforms.get("instagram").limit == 2200
    for p in platforms.PLATFORMS:
        assert p.limit > 0, p.key


def test_markdown_to_html() -> None:
    """Редактор пишет **жирный** — в Telegram должен уйти тег, а не звёздочки."""
    got = adapt.md_to_html("**Привет** и *курсив*")
    assert "<b>Привет</b>" in got, got
    assert "<i>курсив</i>" in got, got
    assert "**" not in got


def test_plain_platform_strips_markup() -> None:
    """ВК и ОК не понимают теги: в тексте не должно быть ни <b>, ни **."""
    vk = platforms.get("vk")
    got = adapt.adapt(vk, "**Жирный** текст")["text"]
    assert "<b>" not in got and "**" not in got, got


def test_cut_by_word_within_limit() -> None:
    """Длинный текст обрезается целым словом и не вылезает за лимит."""
    tg = platforms.get("telegram")
    body = " ".join(["слово"] * 800)
    got = adapt.adapt(tg, body)
    assert len(got["text"]) <= tg.limit, len(got["text"])
    assert got["truncated"] is True
    assert got["text"].endswith(adapt.ELLIPSIS)
    assert "слов" in got["text"], "обрезали посреди слова"


def test_html_stays_balanced_after_cut() -> None:
    """Обрезка внутри **жирного** не должна давать незакрытый тег (Telegram отклонит пост)."""
    tg = platforms.get("telegram")
    body = "**" + " ".join(["слово"] * 300) + "**"
    got = adapt.adapt(tg, body)["text"]
    opened = re.findall(r"<([a-z]+)(?: [^>]*)?>", got)
    closed = re.findall(r"</([a-z]+)>", got)
    assert sorted(opened) == sorted(closed), (got[:120], opened, closed)


def test_unknown_tags_become_text() -> None:
    """Незнакомый Telegram тег превращается в текст — иначе сообщение не уйдёт вообще."""
    got = adapt.balance_html("<div>привет</div>")
    assert "&lt;div&gt;" in got, got


def test_links_appended_once() -> None:
    """Ссылка из поля «ссылки» попадает в каждый пост ровно один раз."""
    got = adapt.adapt_all("Текст", ["telegram", "vk"], ["https://alyaeva.pro/order"])
    for k in ("telegram", "vk"):
        assert got[k]["text"].count("https://alyaeva.pro/order") == 1, got[k]["text"]


def test_signature_only_where_defined() -> None:
    """Подпись @ник — только у Instagram; в Telegram и ВК её быть не должно."""
    got = adapt.adapt_all("Текст", ["instagram", "telegram", "vk"], [],
                          {"instagram": "alyaeva", "telegram": "alyaeva"})
    assert "@alyaeva" in got["instagram"]["text"]
    assert "@alyaeva" not in got["telegram"]["text"]
    assert "@alyaeva" not in got["vk"]["text"]


def test_each_platform_gets_own_length() -> None:
    """Один и тот же материал даёт разные тексты: у Telegram и ВК разные обрезки."""
    body = " ".join(["длинное слово"] * 400)
    got = adapt.adapt_all(body, ["telegram", "vk"])
    assert len(got["telegram"]["text"]) <= 1024 < len(got["vk"]["text"])


def test_unknown_platform_skipped() -> None:
    """Лишняя галочка из старой страницы не роняет остальные площадки."""
    got = adapt.adapt_all("Текст", ["telegram", "myspace"])
    assert list(got.keys()) == ["telegram"]


def test_media_requirement_flag() -> None:
    """Instagram помечен «нужно фото» — страница обязана предупредить до отправки."""
    assert platforms.get("instagram").needs_media is True
    assert platforms.get("telegram").needs_media is False


def _main() -> int:
    """Запуск без pytest: все test_* по очереди, падение — код 1."""
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in tests:
        try:
            fn()
            print("ok  ", name)
        except AssertionError as e:
            bad += 1
            print("FAIL", name, "—", e)
        except Exception as e:  # noqa: BLE001
            bad += 1
            print("ERR ", name, "—", type(e).__name__, e)
    print("итого: %d, провалов: %d" % (len(tests), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_main())
