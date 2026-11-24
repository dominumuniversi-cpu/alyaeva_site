-- Своя аналитика: без внешних сервисов и слежки за людьми (152-ФЗ).
-- Считаем события, а не визиты конкретного человека: ни IP, ни cookie не храним.
CREATE TABLE IF NOT EXISTS site_events (
    id         BIGSERIAL PRIMARY KEY,
    day        DATE NOT NULL DEFAULT current_date,
    kind       TEXT NOT NULL,          -- view | order_open | order_created | login | register
    page       TEXT NOT NULL DEFAULT '',   -- для view: путь страницы
    source     TEXT NOT NULL DEFAULT '',   -- откуда пришёл: search | telegram | vk | direct | other
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_site_events_day ON site_events(day, kind);
