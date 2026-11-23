-- =============================================================
-- alyaeva.pro — схема базы данных
-- Принципы (решение владельца): настройки — в настройках,
-- магазин и цены — в своих таблицах, ничего не смешивать.
-- Все миграции ТОЛЬКО добавляющие (ADD COLUMN / CREATE TABLE IF NOT EXISTS).
-- =============================================================

-- ---------- 1. ПОЛЬЗОВАТЕЛИ И СЕССИИ ----------
CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name     TEXT NOT NULL DEFAULT '',
    phone         TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL DEFAULT 'customer',
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ,
    CONSTRAINT users_role_chk CHECK (role IN ('customer','admin'))
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(lower(email));

-- Сессии: токен хранится хешем, не открытым текстом
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    user_agent TEXT NOT NULL DEFAULT '',
    ip         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

-- ---------- 2. НАСТРОЙКИ (единственное место для настроек) ----------
-- Ключ — путь через точку: telegram.proxy, vk.token, site.phone.
-- Значение — JSON-строка. Секреты хранятся ЗАШИФРОВАННЫМИ (enc:...).
CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    is_secret   BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by  BIGINT REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS app_settings_audit (
    id         BIGSERIAL PRIMARY KEY,
    key        TEXT NOT NULL,
    old_value  TEXT,
    new_value  TEXT,
    changed_by BIGINT REFERENCES users(id),
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_settings_audit_key ON app_settings_audit(key, changed_at DESC);

-- ---------- 3. МАГАЗИН: ТОВАРЫ И ЦЕНЫ ----------
CREATE TABLE IF NOT EXISTS products (
    id          BIGSERIAL PRIMARY KEY,
    slug        TEXT UNIQUE NOT NULL,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    price_rub   NUMERIC(10,2) NOT NULL DEFAULT 0,
    old_price   NUMERIC(10,2),
    unit        TEXT NOT NULL DEFAULT 'шт',
    image_url   TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 100,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_products_active ON products(is_active, sort_order);

-- ---------- 4. ЗАКАЗЫ ----------
CREATE TABLE IF NOT EXISTS orders (
    id           BIGSERIAL PRIMARY KEY,
    number       TEXT UNIQUE NOT NULL,
    user_id      BIGINT REFERENCES users(id) ON DELETE SET NULL,
    customer_name  TEXT NOT NULL,
    customer_phone TEXT NOT NULL,
    customer_email TEXT NOT NULL DEFAULT '',
    comment      TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'new',
    total_rub    NUMERIC(10,2) NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT orders_status_chk CHECK (status IN ('new','in_work','sent','done','cancelled'))
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);

CREATE TABLE IF NOT EXISTS order_items (
    id          BIGSERIAL PRIMARY KEY,
    order_id    BIGINT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id  BIGINT REFERENCES products(id) ON DELETE SET NULL,
    title       TEXT NOT NULL,
    price_rub   NUMERIC(10,2) NOT NULL,
    qty         INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);

-- ---------- 5. БИБЛИОТЕКА ФАЙЛОВ (проекты покупателя → заказ → фото) ----------
-- Структура: у клиента есть проекты, проект привязан к заказу (опционально),
-- файлы лежат в проекте. Один владелец, доступ только ему и админу.
CREATE TABLE IF NOT EXISTS projects (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    order_id   BIGINT REFERENCES orders(id) ON DELETE SET NULL,
    title      TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS project_files (
    id          BIGSERIAL PRIMARY KEY,
    project_id  BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    orig_name   TEXT NOT NULL,
    stored_name TEXT NOT NULL UNIQUE,
    mime_type   TEXT NOT NULL DEFAULT '',
    size_bytes  BIGINT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_project_files_project ON project_files(project_id);
CREATE INDEX IF NOT EXISTS idx_project_files_user ON project_files(user_id);

-- ---------- 6. ШЛЮЗ СООБЩЕНИЙ (по схеме NPC telegram_hub) ----------
CREATE TABLE IF NOT EXISTS message_hub (
    id         BIGSERIAL PRIMARY KEY,
    thread_id  BIGINT,
    direction  TEXT NOT NULL DEFAULT 'in',
    channel    TEXT NOT NULL,
    external_id TEXT,
    author     TEXT NOT NULL DEFAULT '',
    body       TEXT NOT NULL DEFAULT '',
    meta       TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT msg_direction_chk CHECK (direction IN ('in','out'))
);
CREATE INDEX IF NOT EXISTS idx_message_hub_thread ON message_hub(thread_id, id);
CREATE INDEX IF NOT EXISTS idx_message_hub_ext ON message_hub(channel, external_id);

CREATE TABLE IF NOT EXISTS message_threads (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT REFERENCES users(id) ON DELETE SET NULL,
    order_id   BIGINT REFERENCES orders(id) ON DELETE SET NULL,
    channel    TEXT NOT NULL,
    external_chat_id TEXT,
    title      TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_threads_last ON message_threads(last_at DESC);

CREATE TABLE IF NOT EXISTS message_cursors (
    channel     TEXT PRIMARY KEY,
    last_id     BIGINT NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- 7. КОНТЕНТ-ЗАВОД ----------
CREATE TABLE IF NOT EXISTS posts (
    id         BIGSERIAL PRIMARY KEY,
    body       TEXT NOT NULL,
    media_url  TEXT NOT NULL DEFAULT '',
    channels   TEXT NOT NULL DEFAULT 'telegram',
    status     TEXT NOT NULL DEFAULT 'draft',
    publish_at TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    result     TEXT NOT NULL DEFAULT '{}',
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status, publish_at);
