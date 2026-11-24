-- Приложение знает 8 статусов (order_status.py), а в БД было разрешено 5 старых.
-- Итог: в админке падала смена статуса с 500 на «На согласовании макета», «Оплачен»,
-- «В печати» и «Отправлен» — то есть три четверти этапов заказа.
-- Ограничение приводится к словарю приложения: один источник правды.

ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_chk;
ALTER TABLE orders ADD CONSTRAINT orders_status_chk
    CHECK (status IN ('new', 'in_work', 'layout', 'paid', 'printing', 'shipped', 'done', 'cancelled'));
