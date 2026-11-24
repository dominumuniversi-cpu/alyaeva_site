-- Телефон не обязателен: вход идёт по e-mail и паролю.
-- NOT NULL на phone ломал регистрацию с 500, если человек не указал номер.
ALTER TABLE users ALTER COLUMN phone DROP NOT NULL;
