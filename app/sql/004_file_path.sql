-- Путь к файлу хранится в записи и не зависит от владельца.
-- Иначе привязка гостевого заказа к аккаунту ломает ссылку: файл лежит в guest/<проект>/,
-- а после смены user_id код начинает искать его в <user_id>/<проект>/.
ALTER TABLE project_files ADD COLUMN IF NOT EXISTS rel_path text;
UPDATE project_files SET rel_path = 'guest/' || project_id || '/' || stored_name WHERE rel_path IS NULL;
