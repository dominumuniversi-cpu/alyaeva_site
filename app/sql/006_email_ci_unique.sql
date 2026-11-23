-- Слой 1: почта уникальна без учёта регистра.
-- Раньше unique(email) был чувствителен к регистру: "Ivan@mail.ru" и "ivan@mail.ru"
-- проходили как два разных человека, и получались дубли аккаунтов.

UPDATE users SET email = lower(btrim(email)) WHERE email <> lower(btrim(email));
CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_key ON users (lower(btrim(email)));
