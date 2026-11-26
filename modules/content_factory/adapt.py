"""Адаптация одного поста под несколько площадок.

Один материал (текст от человека или от нейросети) + общая ссылка →
готовый текст для каждой выбранной площадки: с разметкой, которую она понимает,
обрезанный по её лимиту и с её подписью в конце.

Почему отдельный файл без базы и без сети: это единственная часть завода, которую надо
проверить честно, числом. Здесь нет ни БД, ни HTTP — только строки на входе и строки на выходе,
поэтому калькулятор проверяется тестом без стенда и без токенов.
"""
from __future__ import annotations

import html
import re

from .platforms import Platform, get

#: Метка обрезки: три точки одним символом — иначе в Telegram «...» занимает три позиции лимита.
ELLIPSIS = "\u2026"

#: Теги, которые понимает Telegram Bot API (parse_mode=HTML). Незнакомые в сообщении
#: приводят к отказу «can't parse entities»: такое сообщение не уходит вообще.
_ALLOWED = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "a", "code", "pre",
            "span", "tg-spoiler", "blockquote"}

#: Символ обратной кавычки для «кода» в Markdown — через chr(96), чтобы не путаться
#: с кавычками Python и не зависеть от того, как файл сохранён.
_TICK = chr(96)


def md_to_html(md: str) -> str:
    """Markdown из редактора → HTML, который понимает Telegram.

    Редактор у человека простой: **жирный**, *курсив*, обратные кавычки для кода, [текст](URL).
    Нейросеть отвечает тем же Markdown. Поэтому сначала снимаем HTML, потом экранируем
    спецсимволы и только потом превращаем управляющие символы в теги — в этом порядке,
    иначе символы самого текста (например, «a < b») превратятся в разметку.
    """
    text = md or ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.escape(text, quote=False)
    # Ссылки: [подпись](адрес) → <a href="адрес">подпись</a>. Адрес экранируем отдельно:
    # кавычка в адресе рвёт тег, и сообщение уходит битым.
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
                  lambda m: '<a href="%s">%s</a>' % (html.escape(m.group(2), quote=True), m.group(1)),
                  text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.S)
    text = re.sub(r"(?<!\w)__(.+?)__(?!\w)", r"<b>\1</b>", text, flags=re.S)
    text = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<i>\1</i>", text, flags=re.S)
    text = re.sub(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)", r"<i>\1</i>", text, flags=re.S)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text, flags=re.S)
    text = re.sub(_TICK + r"([^" + _TICK + r"]+)" + _TICK, r"<code>\1</code>", text)
    return text


def cut(text: str, limit: int) -> str:
    """Обрезать по границе слова, оставив метку обрезки в пределах лимита.

    Раньше обрезка брала первые N символов — и HTML обрывался на середине тега: Telegram
    отвечал «can't parse entities», пост не уходил вовсе. Здесь обрезка идёт по последнему
    пробелу не ближе 60% лимита: лучше короче и целым словом, чем ровно в лимит и полусловом.
    """
    if len(text) <= limit:
        return text
    head = text[: max(1, limit - 1)]
    space = max(head.rfind(" "), head.rfind("\n"))
    if space >= int(limit * 0.6):
        head = head[:space]
    return head.rstrip() + ELLIPSIS


def balance_html(text: str) -> str:
    """Закрыть теги, оставшиеся открытыми, и выкинуть одиночные закрывающие.

    Telegram отклоняет сообщение с незакрытым тегом целиком — пост не уходит вообще.
    Поэтому после обрезки HTML приводим к корректному виду: лишние закрывающие убираем,
    открытые — закрываем в обратном порядке. Незнакомые теги превращаем в текст.
    """
    out: list[str] = []
    stack: list[str] = []
    pos = 0
    for m in re.finditer(r"<(/?)([a-zA-Z][\w-]*)([^>]*)>", text or ""):
        out.append(text[pos:m.start()])
        pos = m.end()
        closing, name = m.group(1) == "/", m.group(2).lower()
        if name not in _ALLOWED:
            out.append(html.escape(m.group(0), quote=False))
            continue
        if closing:
            if name in stack:
                while stack:
                    top = stack.pop()
                    out.append("</%s>" % top)
                    if top == name:
                        break
            continue
        stack.append(name)
        out.append(m.group(0))
    out.append(text[pos:] if text else "")
    while stack:
        out.append("</%s>" % stack.pop())
    return "".join(out)


def signature(platform: Platform, handle: str) -> str:
    """Подпись площадки. {handle} → @аккаунт; пустое значение — подписи нет."""
    sig = (platform.signature or "").strip()
    handle = (handle or "").strip()
    if not sig or not handle:
        return ""
    if not handle.startswith("@") and not handle.startswith("http"):
        handle = "@" + handle
    return sig.replace("{handle}", handle)


def render_text(platform: Platform, body: str, links: list[str] | None = None) -> str:
    """Тело поста в разметке площадки — без обрезки и без подписи.

    Разметка и ссылки делают длину разной для каждой площадки (теги занимают место),
    поэтому это отдельный шаг от обрезки: сначала собрать, потом обрезать ровно то, что уйдёт.
    """
    text = (body or "").strip()
    extra = [str(u).strip() for u in (links or []) if u and str(u).strip()]
    if extra:
        text = text + "\n\n" + "\n".join(extra)
    if platform.markup == "html":
        return md_to_html(text)
    if platform.markup == "markdown":
        return text
    # plain (ВК, ОК, Instagram): разметку не понимают — снимаем управляющие символы,
    # оставив сам текст и адреса ссылок, чтобы человек видел, куда ведёт ссылка.
    plain = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r"\1 (\2)", text)
    plain = re.sub(r"<[^>]+>", "", plain)
    # Управляющие символы Markdown снимаем: ВК и ОК показывают их как есть («**жирный**»),
    # а это выглядит как недоделанный текст, а не как оформление.
    plain = re.sub(r"\*\*(.+?)\*\*", r"\1", plain, flags=re.S)
    plain = re.sub(r"(?<!\w)__(.+?)__(?!\w)", r"\1", plain, flags=re.S)
    plain = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"\1", plain, flags=re.S)
    plain = re.sub(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)", r"\1", plain, flags=re.S)
    plain = re.sub(r"~~(.+?)~~", r"\1", plain, flags=re.S)
    plain = re.sub(_TICK + r"([^" + _TICK + r"]+)" + _TICK, r"\1", plain)
    return plain


def adapt(platform: Platform, body: str, links: list[str] | None = None,
          handle: str = "") -> dict:
    """Готовый текст поста для одной площадки: тело, обрезка, подпись, сколько не влезло.

    Возвращает словарь, а не строку: странице надо показать человеку, сколько символов
    он не увидит, даже когда текст формально влез, — иначе лимит проверяют на глаз.
    """
    sig = signature(platform, handle)
    formatted = render_text(platform, body, links)
    raw_len = len(formatted)
    room = platform.limit - (len(sig) + 2 if sig else 0)
    fitted = cut(formatted, max(1, room))
    full = (fitted + "\n\n" + sig) if sig else fitted
    if platform.markup == "html":
        full = balance_html(full)
    return {
        "platform": platform.key,
        "name": platform.name,
        "text": full,
        "used": len(full),
        "limit": platform.limit,
        # Сколько символов исходного текста не попало в пост. 0 — влезло всё.
        "cut": (raw_len - len(fitted) + len(ELLIPSIS)) if len(fitted) < raw_len else 0,
        "truncated": len(fitted) < raw_len,
        "markup": platform.markup,
        "needs_media": platform.needs_media,
    }


def adapt_all(body: str, platforms: list[str], links: list[str] | None = None,
              handles: dict | None = None) -> dict[str, dict]:
    """Тексты сразу для нескольких площадок. Ключ — ключ площадки, порядок — как передали.

    Неизвестный ключ пропускается молча по одной причине: список приходит из формы,
    и лишняя галочка из устаревшей страницы не должна ронять пост на остальные площадки.
    """
    handles = handles or {}
    out: dict[str, dict] = {}
    for key in platforms:
        p = get(key)
        if not p:
            continue
        out[p.key] = adapt(p, body, links, handles.get(p.key, ""))
    return out
