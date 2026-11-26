"""Проверка шаблонов Jinja2 — вынесено из воркфлоу, чтобы YAML не ломался (#274)."""
import pathlib
import sys

from jinja2 import Environment



# --- Где меню админки, а где его быть не должно (26.11.2026) -------------------
#
# Почему появилось: дважды подряд я «проверял» вёрстку замерами, которые возвращали 0,
# и читал это как успех — владелец увидел админку без меню. Разбор ошибок запишу
# в docs/mistakes.md. Здесь проверка, которая падает сама, в CI, без человека.
#
# Механика: base.html показывает меню, если пользователь админ И адрес начинается
# с /admin. Проверяю отрисовкой, а не глазами.

from types import SimpleNamespace

# Адрес страницы → шаблон, которым она показывается.
MENU_PAGES = {
    "/admin": "admin_orders.html",
    "/admin/posts": "admin_posts.html",
    "/admin/inbox": "admin_inbox.html",
    "/admin/hooks": "hooks_help.html",
    "/admin/settings": "admin_settings.html",
}
SITE_PAGES = {
    "/ceny": "prices.html",
    "/order": "order.html",
    "/cabinet": "cabinet.html",
    "/settings": "settings.html",
    "/oplata": "oplata.html",
}


def _context(path: str) -> dict:
    """Правдоподобный контекст страницы: админ, запрос, контакты, настройки."""
    req = SimpleNamespace(url=SimpleNamespace(path=path), query_params={},
                          headers={}, cookies={})
    return dict(user={"role": "admin", "email": "test@example.invalid", "name": "Проверка"},
                request=req, admin_url="/admin", links={}, settings={}, counts=None,
                csrf_token="x", rub=lambda v: v)


def check_menu(env: Environment) -> list[str]:
    """Проверяет меню админки отрисовкой всех значимых страниц."""
    env.filters.setdefault("rub", lambda v: str(v))
    problems: list[str] = []
    for path, name in MENU_PAGES.items():
        try:
            html = env.get_template(name).render(**_context(path))
        except Exception as e:  # noqa: BLE001 — падение шаблона и есть провал
            problems.append(f"{path}: страница не отрисовалась ({type(e).__name__})")
            continue
        if "admin-subnav" not in html:
            problems.append(f"{path}: меню админки ПРОПАЛО")
        if "admin-shell" not in html:
            problems.append(f"{path}: нет каркаса admin-shell")
    for path, name in SITE_PAGES.items():
        try:
            html = env.get_template(name).render(**_context(path))
        except Exception as e:  # noqa: BLE001
            problems.append(f"{path}: страница не отрисовалась ({type(e).__name__})")
            continue
        if "admin-subnav" in html:
            problems.append(f"{path}: меню админки НА СТРАНИЦЕ САЙТА")
    return problems


def main() -> int:
    """Разбирает все шаблоны и проверяет меню админки."""
    env = Environment()
    bad = []
    for p in sorted(pathlib.Path("app/templates").glob("*.html")):
        try:
            env.parse(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 — показываем ученику любую ошибку разбора
            bad.append(f"{p}: {e}")
    if bad:
        print("\n".join(bad))
        return 1

    # Вторая проверка — отрисовка: нужен загрузчик и фильтры приложения.
    from jinja2 import Environment as _Env, FileSystemLoader
    real = _Env(loader=FileSystemLoader("app/templates"), autoescape=True)
    problems = check_menu(real)
    if problems:
        print("\n".join(problems))
        return 1
    print("шаблоны в порядке, меню админки на месте")
    return 0


if __name__ == "__main__":
    sys.exit(main())