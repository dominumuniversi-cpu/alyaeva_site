#!/usr/bin/env node
// Первичная установка администратора: задаёт пароль для существующего админа
// или создаёт его, если записи нет. Запуск: node scripts\set-admin-password.js <email> <пароль>
const { spawnSync } = require('child_process');
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe';
const [, , email, password] = process.argv;
if (!email || !password || password.length < 8) {
  console.error('нужно: node scripts\\set-admin-password.js <email> <пароль 8+ символов>');
  process.exit(1);
}
// Хешируем внутри контейнера приложения — там есть bcrypt, и пароль не попадает в psql-историю.
const py = `
import db, sys
email = sys.argv[1].lower()
pw = sys.argv[2]
with db.pool.connection() as conn:
    row = conn.execute("SELECT id FROM users WHERE lower(email) = %s", (email,)).fetchone()
if row:
    db.set_password(row["id"], pw)
    print("пароль обновлён для", email, "id=", row["id"])
else:
    uid = db.create_user(email, pw, "Алёна Аляева", "admin")
    print("админ создан:", email, "id=", uid)
`;
const r = spawnSync(DOCKER, ['exec', 'alyaeva-web', 'python', '-c', py, email, password], {encoding:'utf8'});
console.log((r.stdout || '').trim() || (r.stderr || '').trim());
process.exit(r.status || 0);
