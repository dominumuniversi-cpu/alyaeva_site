"""Проверка шаблонов Jinja2 — вынесено из воркфлоу, чтобы YAML не ломался (#274)."""
import pathlib
import sys

from jinja2 import Environment


def main() -> int:
    """Разбирает все шаблоны; возвращает 1, если хоть один не разобрался."""
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
    print("шаблоны в порядке")
    return 0


if __name__ == "__main__":
    sys.exit(main())