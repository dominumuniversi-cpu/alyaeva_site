"""Нейросети и поисковые контуры: подключение и отключение кнопки «Сочинить».

Правило владельца: если ни одна нейросеть не подключена — возможность автоматической
генерации выключена (кнопка серая) с уведомлением и просьбой подключить.

Реализация «подключено» — одна: есть строка в cf_llm с enabled = true и заполненным токеном.
Ключи хранятся шифром (так же, как токены мессенджеров): в базе только «enc:...».

Провайдеры описаны данными. Все четыре говорят на языке OpenAI (chat/completions),
поэтому один запрос обслуживает все: Perplexity — тот же формат.
"""
from __future__ import annotations

import time

import httpx

from . import crypto, dbsession, settings

#: Провайдер → адрес API, модель по умолчанию, подсказка где взять ключ.
PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "hint": "Ключ — в кабинете platform.deepseek.com → API keys. Работает из России без прокси.",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "hint": "Ключ — платформа OpenAI → API keys. Из России нужен прокси (поле «Прокси» ниже).",
    },
    "perplexity": {
        "label": "Perplexity (поисковый контур)",
        "base_url": "https://api.perplexity.ai",
        "model": "sonar",
        "hint": "Ключ — кабинет perplexity.ai → API. Сочиняет с опорой на свежие источники из интернета.",
    },
    "openrouter": {
        "label": "OpenRouter (любая модель)",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "deepseek/deepseek-chat",
        "hint": "Один ключ — сотни моделей. Имя модели — из каталога openrouter.ai/models.",
    },
}

#: Провайдеры, которые сами ищут в интернете: на странице подписаны как «с разведкой».
RESEARCH = {"perplexity"}


def _encrypt(value: str) -> str:
    """Зашифровать ключ шифровальщиком сайта (подключён через cf.secure)."""
    return crypto.encrypt(value or "")


def _decrypt(value: str) -> str:
    """Расшифровать ключ. Старое значение без префикса вернётся как есть."""
    return crypto.decrypt(value or "")


def ensure_builtin() -> None:
    """Завести строки известных провайдеров — один раз, выключенными и без ключа.

    ON CONFLICT DO NOTHING: повторная установка не затирает введённое человеком.
    """
    for key, p in PROVIDERS.items():
        dbsession.tx(
            """INSERT INTO cf_llm (provider, label, base_url, model, token, enabled)
               VALUES (%s, %s, %s, %s, '', false) ON CONFLICT (provider) DO NOTHING""",
            (key, p["label"], p["base_url"], p["model"]),
        )


def rows() -> list[dict]:
    """Все провайдеры с пометкой, есть ли ключ. Сам ключ наружу не отдаём."""
    got = {r["provider"]: r for r in dbsession.run("SELECT * FROM cf_llm")}
    out = []
    for key, meta in PROVIDERS.items():
        r = got.get(key) or {}
        out.append({
            "provider": key,
            "label": r.get("label") or meta["label"],
            "base_url": r.get("base_url") or meta["base_url"],
            "model": r.get("model") or meta["model"],
            "has_key": bool(_decrypt(r.get("token") or "")),
            "enabled": bool(r.get("enabled")),
            "research": key in RESEARCH,
            "hint": meta["hint"],
        })
    return out


def active() -> dict | None:
    """Подключённая модель для генерации: включена, ключ есть, адрес заполнен. Иначе None."""
    got = {r["provider"]: r for r in dbsession.run("SELECT * FROM cf_llm")}
    for key in PROVIDERS:
        r = got.get(key) or {}
        token = _decrypt(r.get("token") or "").strip()
        if r.get("enabled") and token and (r.get("base_url") or "").strip():
            return {"provider": key, "label": PROVIDERS[key]["label"],
                    "base_url": r["base_url"].strip(), "model": (r.get("model") or "").strip(),
                    "token": token, "research": key in RESEARCH}
    return None


def enabled() -> bool:
    """Есть ли чем сочинять. От этого зависит, серая кнопка или живая."""
    return active() is not None


def save(provider: str, token, model, base_url, enabled, ) -> None:
    """Сохранить подключение. Пустое поле не меняет прежнее — как у токенов в «Интеграции».

    Стирание по пустому полю уже ломало отправку («сама по себе» отключилась интеграция),
    поэтому здесь тот же порядок: пусто — значит «оставить как было».
    """
    r = dbsession.one("SELECT * FROM cf_llm WHERE provider = %s", (provider,)) or {}
    new_token = r.get("token") or ""
    if token is not None and str(token).strip():
        new_token = _encrypt(str(token).strip())
    dbsession.tx(
        """INSERT INTO cf_llm (provider, label, base_url, model, token, enabled, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, now())
           ON CONFLICT (provider) DO UPDATE SET base_url = EXCLUDED.base_url,
             model = EXCLUDED.model, token = EXCLUDED.token, enabled = EXCLUDED.enabled,
             updated_at = now()""",
        (provider, PROVIDERS[provider]["label"],
         (base_url or "").strip() or r.get("base_url") or PROVIDERS[provider]["base_url"],
         (model or "").strip() or r.get("model") or PROVIDERS[provider]["model"],
         new_token, bool(enabled) if enabled is not None else bool(r.get("enabled"))),
    )


def clear_key(provider: str) -> None:
    """Убрать ключ и выключить провайдера — кнопка «Отключить» на странице настроек."""
    dbsession.tx("UPDATE cf_llm SET token = '', enabled = false, updated_at = now() WHERE provider = %s",
                 (provider,))


def chat(prompt: str, system: str = "", max_tokens: int = 700, temperature: float = 0.8) -> dict:
    """Один запрос к подключённой модели.

    Возвращает {ok, text, detail, ms, provider, model}. Никогда не бросает:
    человек нажал кнопку — он должен получить ответ словами, а не 500.
    """
    conn = active()
    if not conn:
        return {"ok": False, "text": "", "detail": "нейросеть не подключена", "ms": 0}
    started = time.time()
    body = {
        "model": conn["model"],
        "messages": ([{"role": "system", "content": system}] if system else []) +
                    [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    proxy = str(settings.get("cf.llm.proxy", "") or "").strip()
    try:
        with httpx.Client(timeout=60.0, proxy=(proxy or None)) as client:
            r = client.post(conn["base_url"].rstrip("/") + "/chat/completions", json=body,
                            headers={"Authorization": "Bearer " + conn["token"]})
        ms = int((time.time() - started) * 1000)
        if r.status_code >= 400:
            return {"ok": False, "text": "", "ms": ms, "provider": conn["provider"],
                    "model": conn["model"],
                    "detail": "сервис ответил кодом %s: %s" % (r.status_code, (r.text or "")[:180])}
        j = r.json()
        text = (((j.get("choices") or [{}])[0] or {}).get("message") or {}).get("content") or ""
        return {"ok": bool(text.strip()), "text": text.strip(), "ms": ms,
                "detail": "" if text.strip() else "сервис вернул пустой ответ",
                "provider": conn["provider"], "model": conn["model"]}
    except Exception as e:  # noqa: BLE001 — сеть и ключ — частые отказы, их показываем словами
        return {"ok": False, "text": "", "ms": int((time.time() - started) * 1000),
                "detail": str(e)[:200], "provider": conn["provider"], "model": conn["model"]}
