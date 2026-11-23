# Манифест 10 — Кнопки (единый компонент)

## Варианты
| Вариант | Фон | Текст | Применение |
|---|---|---|---|
| primary | #4B4690 | #FFFFFF | главная CTA (Telegram, «Заказать сказку») |
| secondary | #FFFFFF + border 2px #4B4690 | #4B4690 | «Смотреть работы», «Узнать стоимость» |
| messenger-tg | #2AABEE (или бренд-акцент) | #FFF | Telegram |
| messenger-wa | #25D366 | #FFF | WhatsApp |
| messenger-vk | #0077FF | #FFF | ВКонтакте |

## Базовые стили (все варианты)
- display: inline-flex; align-items/justify-content: center; min-height: 54px; padding: 0 30px;
- border-radius: 999px (pill); font: Nunito 700, 16–17px;
- hover: затемнение фона на 10% + translateY(-1px), transition 0.2s;
- focus-visible: outline 3px rgba(75,70,144,.4) — доступность;
- внешние ссылки: target="_blank" rel="noopener noreferrer".

## Антипаттерны
- Не инлайнить стили в каждый тег <a> (в текущей выгрузке так) — собрать класс .btn .btn--primary и т.д.
