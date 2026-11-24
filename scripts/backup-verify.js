#!/usr/bin/env node
/**
 * Проверка бекапа: развернуть свежий дамп во временную базу и сверить с живой.
 *
 * Зачем: бекап, который ни разу не восстанавливали, — это надежда, а не бекап.
 * Сверяем число таблиц, пользователей и заказов, затем временную базу удаляем
 * в том же запуске — даже если проверка упала.
 *
 *   node scripts\backup-verify.js            # свежий дамп из каталога бекапов
 *   node scripts\backup-verify.js --keep     # оставить временную базу для разбора
 *
 * Код возврата: 0 — восстановление сошлось; 1 — не сошлось или дамп не поднялся.
 */
const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const DOCKER = "C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe";
const DIR = "E:\\DockerData\\alyaeva\\backups";
const PG = "sc-main-pg";
const TEST_DB = "alyaeva_restore_test";
const LOG = "E:\\DockerData\\alyaeva\\logs\\backup-verify.log";
const KEEP = process.argv.includes("--keep");

/** Запустить docker, вернуть { out, err }. */
function docker(args, timeout, input) {
  try {
    const r = execFileSync(DOCKER, args, { encoding: "utf8", timeout: timeout || 180000, stdio: ["pipe", "pipe", "pipe"], input: input || undefined });
    return { out: (r || "").toString().trim(), err: "" };
  } catch (e) {
    return { out: (e.stdout || "").toString().trim(), err: (e.stderr || e.message || "").toString().trim() };
  }
}

/** Записать строку в лог проверки. */
function log(line) {
  fs.mkdirSync(path.dirname(LOG), { recursive: true });
  fs.appendFileSync(LOG, new Date().toISOString() + " " + line + "\n");
  console.log(line);
}

/** psql внутри базы: вернуть строку вывода. */
function psql(sql, db) {
  return docker(["exec", PG, "psql", "-U", "alyaeva_app", "-d", db, "-tAc", sql]);
}

/** Число таблиц и строк в основных таблицах базы. */
function snapshot(db) {
  const tables = psql("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'", db);
  const users = psql("SELECT count(*) FROM users", db);
  const orders = psql("SELECT count(*) FROM orders", db);
  return { tables: (tables.out || "").trim(), users: (users.out || "").trim(), orders: (orders.out || "").trim() };
}

/** Точка входа. */
function main() {
  const dumps = fs.readdirSync(DIR).filter((f) => f.startsWith("db-") && f.endsWith(".sql.gz"));
  if (!dumps.length) { log("ОШИБКА: нет ни одного дампа в " + DIR); process.exit(1); }
  const newest = dumps.sort().pop();
  const file = path.join(DIR, newest);
  log("проверяю дамп: " + newest + " (" + (fs.statSync(file).size / 1024).toFixed(1) + " КБ)");

  const live = snapshot("alyaeva");
  if (!live.tables || live.tables === "0") { log("ОШИБКА: живая база не отвечает, сверять не с чем"); process.exit(1); }

  // Временную базу создаёт и сносит суперпользователь: у пользователя приложения права на
  // создание баз нет (проверено: `permission denied to create database`).
  docker(["exec", PG, "psql", "-U", "sc", "-d", "postgres", "-c", "DROP DATABASE IF EXISTS " + TEST_DB], 60000);
  const created = docker(["exec", PG, "psql", "-U", "sc", "-d", "postgres", "-c", "CREATE DATABASE " + TEST_DB + " OWNER alyaeva_app"], 60000);
  if (created.err && !/already exists/.test(created.err)) { log("ОШИБКА: временную базу не создать: " + created.err.slice(0, 200)); process.exit(1); }

  let ok = false;
  try {
    // Файл уходит в контейнер через stdin: так не нужно монтировать диск хоста в базу.
    const restored = docker(["exec", "-i", PG, "sh", "-c",
      "gunzip -c | psql -U alyaeva_app -d " + TEST_DB + " -q -v ON_ERROR_STOP=1"], 1800000, fs.readFileSync(file));
    if (restored.err && /ERROR/.test(restored.err)) {
      log("ОШИБКА восстановления: " + restored.err.split("\n").slice(0, 3).join(" "));
    } else {
      const back = snapshot(TEST_DB);
      ok = back.tables === live.tables && back.users === live.users && back.orders === live.orders;
      log("живая: таблиц " + live.tables + ", пользователей " + live.users + ", заказов " + live.orders);
      log("из дампа: таблиц " + back.tables + ", пользователей " + back.users + ", заказов " + back.orders);
      log(ok ? "ВОССТАНОВЛЕНИЕ СОШЛОСЬ" : "РАСХОЖДЕНИЕ: цифры не совпали");
    }
  } finally {
    if (!KEEP) docker(["exec", PG, "psql", "-U", "sc", "-d", "postgres", "-c", "DROP DATABASE IF EXISTS " + TEST_DB], 60000);
  }
  process.exit(ok ? 0 : 1);
}

main();