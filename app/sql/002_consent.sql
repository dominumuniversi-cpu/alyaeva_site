-- 152-ФЗ: отметка о согласии на обработку персональных данных
ALTER TABLE orders ADD COLUMN IF NOT EXISTS consent_at timestamptz;
