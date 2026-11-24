#!/usr/bin/env node
/**
 * Бэкап alyaeva.pro: дамп базы и папка фото.
 *
 * Без бэкапа упавшая база уносит заказы, клиентов и переписку — восстановить нечем.
 * Файлы фото хранятся отдельно и вне базы, поэтому сохраняются вместе с дампом:
 * иначе восстановленный заказ ссылается на несуществующие картинки.
 *
 *   node scripts\\backup.js            — сделать бэкап
 *   node scripts\\backup.js --check    — только показать, что уже есть
 *
 * Пароль базы берётся из E:\\DockerData\\alyaeva\\.env и передаётся внутри контейнера,
 * а не аргументом: иначе он виден в списке процессов машины.
 */
const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe';
const ENV_FILE = 'E:\\DockerData\\alyaeva\\.env';
const DIR = 'E:\\DockerData\\alyaeva\\backups';
const UPLOADS = 'E:\\DockerData\\alyaeva\\uploads';
const LOG = 'E:\\DockerData\\alyaeva\\logs\\backup.log';
const KEEP_DAYS = 14;

/** Строка в лог и на экран. */
function log(msg) {
  const line = '[' + new Date().toISOString() + '] ' + msg;
  console.log(line);
  try { fs.mkdirSync(path.dirname(LOG), { recursive: true }); fs.appendFileSync(LOG, line + '\n'); } catch (e) {}
}

/** Запуск docker без падения процесса. */
function docker(args, input, timeout) {
  return spawnSync(DOCKER, args, {
    encoding: 'utf8', timeout: timeout || 600000, input: input || '',
    maxBuffer: 512 * 1024 * 1024,
  });
}

/** Настройки подключения из .env — пароль не печатаем. */
function readEnv() {
  const out = {};
  for (const line of fs.readFileSync(ENV_FILE, 'utf8').split(/\r?\n/)) {
    const s = line.trim();
    if (!s || s.startsWith('#') || !s.includes('=')) continue;
    const i = s.indexOf("=");
    out[s.slice(0, i).trim()] = s.slice(i + 1).trim();
  }
  return out;
}

/** Сколько файлов фото и каков их общий вес. */
function uploadsStats() {
  let n = 0, bytes = 0;
  const walk = (dir) => {
    let items = [];
    try { items = fs.readdirSync(dir, { withFileTypes: true }); } catch (e) { return; }
    for (const it of items) {
      const p = path.join(dir, it.name);
      if (it.isDirectory()) walk(p);
      else { n++; try { bytes += fs.statSync(p).size; } catch (e) {} }
    }
  };
  walk(UPLOADS);
  return { n, bytes };
}

/** Сделать дамп базы и архив фото, удалить старое. Возвращает код выхода. */
function main() {
  const checkOnly = process.argv.includes("--check");
  fs.mkdirSync(DIR, { recursive: true });
  const env = readEnv();
  const db = env.DB_NAME || "alyaeva";
  const user = env.DB_USER || "alyaeva_app";
  const pg = env.DB_HOST_PG || env.DB_HOST || "sc-main-pg";
  const pass = env.DB_PASSWORD || "";

  const dumps = fs.readdirSync(DIR).filter((x) => x.endsWith(".sql.gz"));
  if (checkOnly) {
    const st = uploadsStats();
    log("бэкапов баз: " + dumps.length + " | фото: " + st.n + " файлов");
    const newest = dumps.slice().sort().pop();
    log(newest ? "последний: " + newest : "бэкапов нет");
    return 0;
  }
  if (!pass) { log("ОШИБКА: в .env нет DB_PASSWORD"); return 1; }

  const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const dump = path.join(DIR, "db-" + stamp + ".sql.gz");

  // Пароль уходит в переменную окружения контейнера, а не в аргументы:
  // иначе он виден в списке процессов машины любому, кто посмотрит.
  const inner = "PGPASSWORD=" + JSON.stringify(pass) + " pg_dump -U " + user + " -d " + db +
                " --no-owner --no-privileges | gzip -9 | base64 -w0";
  log("дамп базы " + db);
  const r = docker(["exec", pg, "sh", "-c", inner], "", 900000);
  const b64 = (r.stdout || "").trim();
  const err = (r.stderr || "").trim();
  // Из контейнера забираем base64: обычный двоичный поток exec режет на строках
  // и дамп приходит битым. После раскодирования проверяем первые байты gzip.
  const buf = Buffer.from(b64, "base64");
  if (r.status !== 0 || buf.length < 2 || buf[0] !== 0x1f || buf[1] !== 0x8b) {
    log("ОШИБКА дампа: " + (err || "пустой вывод").slice(0, 300));
    return 1;
  }
  fs.writeFileSync(dump, buf);
  const kb = Math.round(fs.statSync(dump).size / 1024);
  log("готово: " + path.basename(dump) + " (" + kb + " КБ)");

  // Фото лежат отдельно от базы и нужны вместе с ней.
  const imgs = uploadsStats();
  if (imgs.n > 0) {
    const dirTar = path.join(DIR, "uploads");
    fs.mkdirSync(dirTar, { recursive: true });
    const tarName = path.join(dirTar, "uploads-" + stamp.slice(0, 10) + ".tar");
    const rs = docker(["exec", "alyaeva-web", "sh", "-c", "tar -cf - -C /data uploads | base64 -w0"], "", 900000);
    const tarBuf = Buffer.from((rs.stdout || "").trim(), "base64");
    if (rs.status === 0 && tarBuf.length > 512) {
      fs.writeFileSync(tarName, tarBuf);
      log("фото: " + path.basename(tarName) + " (" + Math.round(fs.statSync(tarName).size / 1024) + " КБ, " + imgs.n + " файлов)");
    } else {
      log("ВНИМАНИЕ: фото не собраны — " + (rs.stderr || "").trim().slice(0, 200));
    }
  } else {
    log("фото: пока пусто, архив не нужен");
  }

  // Ротация: без неё папка тихо растёт до заполнения диска.
  const limit = Date.now() - KEEP_DAYS * 86400000;
  let removed = 0;
  for (const x of fs.readdirSync(DIR)) {
    if (!x.endsWith(".sql.gz")) continue;
    const p = path.join(DIR, x);
    try { if (fs.statSync(p).mtimeMs < limit) { fs.unlinkSync(p); removed++; } } catch (e) {}
  }
  if (removed) log("удалено старых дампов: " + removed);
  return 0;
}

process.exit(main());
