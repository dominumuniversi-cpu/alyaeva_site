"""Автогенерация текста поста: бриф → материал → варианты под площадки.

Две части разделены нарочно:
1) brief_prompt() — что просим у модели (тема, стиль, длина, без пояснений);
2) generate() — вызов модели, запись истории и разбор ответа на площадки.

Модель пишет обычный текст с короткими абзацами и эмодзи «по делу». Разметку под каждую
площадку делает адаптер (adapt.py), а не модель: у неё нет знания лимитов Telegram и правил
ВК, и она уверенно врёт про длину.
"""
from __future__ import annotations

from . import adapt, dbsession, llm, platforms, settings

#: Стиль сайта: тёплый, конкретный, без канцелярита. Правится в настройках (cf.style).
STYLE_DEFAULT = (
    "Пиши тепло, по-человечески и конкретно. Без канцелярита, без шаблонных зачинов, "
    "без обещаний результата. Короткие абзацы по 1–2 предложения. Эмодзи — 2–4 на пост, "
    "по делу. Не выдумывай цифр, цен и сроков: если их не дали, о них не говори."
)


def brief_prompt(topic: str, kind: str = "", extra: str = "", research: bool = False) -> str:
    """Текст запроса к модели. Отдельная функция — её видно человеку в истории генераций."""
    style = str(settings.get("cf.style", "") or "").strip() or STYLE_DEFAULT
    site = str(settings.get("site.url", "") or "").rstrip("/")
    parts = ["Тема поста: " + (topic or "").strip()]
    if kind.strip():
        parts.append("Что это за пост: " + kind.strip())
    if extra.strip():
        parts.append("Дополнительно: " + extra.strip())
    parts.append("Стиль: " + style)
    if site:
        parts.append("Сайт: " + site)
    if research:
        parts.append("Если можешь — опирайся на свежие факты из интернета.")
    parts.append("Верни только текст поста: без заголовков, пояснений и markdown-разметки.")
    return "\n".join(parts)


def generate(topic: str, kind: str = "", extra: str = "", created_by=None,
             post_id=None) -> dict:
    """Сочинить пост и разложить его по площадкам. Не подключено — честный отказ словами."""
    conn = llm.active()
    if not conn:
        return {"ok": False, "text": "", "variants": {},
                "error": "Автогенерация недоступна: нейросеть не подключена."}
    prompt = brief_prompt(topic, kind, extra, conn["research"])
    got = llm.chat(prompt,
                   system="Ты — редактор блога небольшой творческой студии. Пиши на русском.",
                   max_tokens=int(settings.get("cf.llm.max_tokens", 800) or 800))
    # История пишется даже при отказе: без неё не понять, почему кнопка «не работает».
    dbsession.tx(
        "INSERT INTO cf_generations (post_id, provider, model, brief, answer, ok, detail, ms, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (post_id, conn["provider"], conn["model"], prompt, (got.get("text") or "")[:4000],
         bool(got.get("ok")), (got.get("detail") or "")[:300], int(got.get("ms") or 0), created_by),
    )
    if not got.get("ok"):
        return {"ok": False, "text": "", "variants": {},
                "error": "Нейросеть не ответила: " + (got.get("detail") or "пустой ответ")}
    text = (got.get("text") or "").strip()
    handles = {k: str(settings.get("cf.targets." + k + ".handle", "") or "") for k in platforms.keys()}
    chosen = [k for k in platforms.keys()
              if str(settings.get("cf.targets." + k + ".address", "") or "").strip()]
    variants = adapt.adapt_all(text, chosen or list(platforms.keys()), [], handles)
    return {"ok": True, "text": text, "variants": variants, "provider": conn["label"],
            "model": conn["model"], "ms": got.get("ms") or 0, "error": ""}


def history(limit: int = 10) -> list[dict]:
    """Последние генерации — чтобы видели, что просили и что вышло."""
    return dbsession.run("SELECT * FROM cf_generations ORDER BY id DESC LIMIT %s", (limit,))


def advice() -> dict:
    """Что показать человеку, пока нейросеть не подключена.

    Три вещи: зачем это, какой сервис взять первым и что будет после подключения.
    Четыре строки одинаковых «подключите» человек пролистывает не читая.
    """
    return {
        "first": "DeepSeek",
        "why": "пишет текст поста по вашему описанию",
        "research": "Perplexity",
        "research_why": "то же, но со свежими фактами и ссылками на источники",
        "after": "кнопка «Сочинить» станет активной, а рядом появится название сервиса",
    }
