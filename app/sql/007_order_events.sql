-- История заказа. Покупатель видит, что уже произошло, а не одну строчку статуса.
CREATE TABLE IF NOT EXISTS order_events (
    id         SERIAL PRIMARY KEY,
    order_id   INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,          -- created | photos | status | comment
    status     TEXT,                   -- для kind='status' — на что сменился
    body       TEXT,                   -- свободный текст (например, «загружено 5 фото»)
    author     TEXT NOT NULL DEFAULT 'system',   -- client | admin | system
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_order_events_order ON order_events(order_id, created_at);

-- Уже существующим заказам — событие «создан», чтобы лента не была пустой.
INSERT INTO order_events (order_id, kind, body, created_at)
SELECT o.id, 'created', 'Заказ создан', o.created_at
FROM orders o
WHERE NOT EXISTS (SELECT 1 FROM order_events e WHERE e.order_id = o.id AND e.kind = 'created');
