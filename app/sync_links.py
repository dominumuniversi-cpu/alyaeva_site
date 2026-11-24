"""Перенос ссылок на мессенджеры из настроек сайта в статичную главную.

Настройки сайта — единственная правда о контактах: их правят в /admin/settings,
и они же используются в кабинете, админке и подвале приложения.
Главная страница — статический файл (build/dist/index.html), и исторически ссылки
там были вписаны руками. Две правды расходятся при первом изменении (#46).

Этот модуль переписывает ссылки в главной по настройкам. Вызывается из
/admin/settings при сохранении — владелец правит в одном месте и видит результат везде.

Если файла главной нет (работаем не на том хосте) — молча выходим:
настройки должны сохраняться в любом случае.
"""
import html as _html
import os
import re

# Куда кладём: тот же путь, что у deploy-site.js. Можно переопределить переменной окружения.
SITE_INDEX = os.getenv("SITE_INDEX", "/srv/site/index.html")

# Настройка → как ссылка выглядит в главной.
# Ключи — как в site_links(); значения — порядок, в котором их искать в файле.
_PATTERNS = {
    "tg_url": r'https://t\.me/[A-Za-z0-9_]+',
    "wa_url": r'https://wa\.me/\d+',
    "vk_url": r'https://vk\.(?:com|ru)/[A-Za-z0-9_.]+',
    "inst_url": r'https://www\.instagram\.com/[A-Za-z0-9_.]+',
}


def apply_links(values: dict) -> int:
    """Переписать ссылки в главной. Вернуть число замен.

    values — словарь site.* из настроек (site.telegram, site.vk, ...).
    Пустое значение ссылку НЕ трогает: удалять блоки из статики мы не умеем,
    а обнулять ссылку в шапке — хуже, чем оставить прежнюю.
    """
    path = SITE_INDEX
    if not os.path.exists(path):
        return 0

    mapping = {
        "tg_url": values.get("site.telegram", ""),
        "wa_url": values.get("site.whatsapp", ""),
        "vk_url": values.get("site.vk", ""),
        "inst_url": values.get("site.instagram", ""),
    }

    with open(path, encoding="utf-8") as f:
        text = f.read()

    replaced = 0
    original = text
    for key, url in mapping.items():
        url = (url or "").strip()
        if not url:
            continue
        pattern = re.compile(_PATTERNS[key])
        # html.escape: ссылка попадёт в атрибут href, кавычки внутри сломали бы разметку.
        safe = _html.escape(url, quote=True)
        text, n = pattern.subn(safe, text)
        replaced += n

    if replaced and text != original:
        # Пишем через временный файл: недописанный index.html = сайт без главной.
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)

    return replaced
