-- Гостевой заказ: клиент ещё не зарегистрирован, владельца проекта нет.
-- Колонки не должны требовать user_id, иначе анонимный заказ не может приложить фото.
ALTER TABLE projects ALTER COLUMN user_id DROP NOT NULL;
ALTER TABLE project_files ALTER COLUMN user_id DROP NOT NULL;
