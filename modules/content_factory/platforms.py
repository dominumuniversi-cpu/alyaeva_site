"""Площадки контент-завода: одна настройка на каждую — лимит, разметка, подпись, как зовётся.

Правило модуля: здесь НЕТ ничего про конкретный сайт. Площадка — это данные
(имя, лимит, разметка, подпись), а текст поста собирается кодом, общим для всех.

Почему площадки описаны данными, а не классами на каждую: площадки отличаются друг от друга
тремя вещами (сколько влезает, какая разметка, что дописать в конце). Класс на каждую — это
шесть файлов, в которых один и тот же попробуй-сохрани-обрежь. Добавить седьмую площадку —
одна запись в PLATFORMS. Где нужно больше (реальный вызов API), там — функция в channels.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Platform:
    """Описание площадки. Заморожено: одна правка настройки не должна менять поведение во всём прогоне."""

    key: str                     # внутренний ключ, латиницей: telegram, vk, ok, instagram, webhook
    name: str                    # как называть по-человечески
    limit: int                   # сколько символов влезает в один пост
    markup: str = "plain"        # html | markdown | plain — что понимает площадка
    #: Аббревиатура для узких карточек в редакторе (на телефоне полное имя не влезает).
    short: str = ""
    #: Подпись канала в конце поста. {handle} заменится на @аккаунт из настроек.
    #: Пусто — подписи не добавляем (на сайте и в вебхуке она не нужна).
    signature: str = ""
    #: Нужно ли фото. Instagram без фото не публикует пост вовсе.
    needs_media: bool = False
    #: Владелец площадки: "site" — отправка внутри этого приложения (встроенный канал),
    #: "config" — адрес обрабатывается настройками сайта (webhook).
    group: str = "builtin"
    #: Что нужно, чтобы площадка стала рабочей — словами для человека на странице настроек.
    needs: str = ""


#: Порядок в этом списке = порядок карточек на странице и порядок кнопок. Не сортировать по алфавиту:
#: владелец называет Telegram первым, за ним ВК, потом ОК и Instagram.
PLATFORMS: tuple[Platform, ...] = (
    Platform(
        key="telegram", name="Telegram", short="TG", limit=1024, markup="html",
        signature="", needs="токен бота и @канал (в «Интеграции»)",
    ),
    Platform(
        key="vk", name="ВКонтакте", short="VK", limit=16000, markup="plain",
        signature="", needs="токен сообщества и id группы (в «Интеграции»)",
    ),
    Platform(
        key="ok", name="Одноклассники", short="OK", limit=24000, markup="plain",
        signature="", needs="токен приложения и id группы ОК",
    ),
    Platform(
        key="instagram", name="Instagram", short="IG", limit=2200, markup="plain",
        signature="{handle}", needs_media=True,
        needs="Business-аккаунт, связанный со страницей Facebook, и токен",
    ),
    Platform(
        key="webhook", name="Своя площадка", short="API", limit=4000, markup="plain",
        group="config", signature="",
        needs="адрес вашего API — пост уйдёт туда POST-запросом с JSON",
    ),
)

_BY_KEY = {p.key: p for p in PLATFORMS}


def get(key: str) -> Platform | None:
    """Площадка по ключу. Неизвестный ключ — None, а не исключение: настройки приходят из формы."""
    return _BY_KEY.get(str(key or "").strip().lower())


def keys() -> list[str]:
    """Ключи всех площадок в человеческом порядке."""
    return [p.key for p in PLATFORMS]
