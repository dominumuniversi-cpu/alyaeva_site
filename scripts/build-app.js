#!/usr/bin/env node
/**
 * Сборка образа приложения одной командой:
 *   node scripts/build-app.js                          → alyaeva-web:latest
 *   node scripts/build-app.js alyaeva-web:<sha>        → образ с именем
 *
 * Зачем отдельный скрипт: контент-завод и другие переносимые модули живут в modules/
 * (вне app/), а контекст сборки — app/. Без копирования в образ попадёт сайт без модулей,
 * и он падает на старте «No module named modules». Это уже случилось при первом деплое
 * контент-завода — поэтому копирование теперь часть сборки, а не памятка человеку.
 *
 * Что делает:
 *   1. стирает app/modules и копирует modules/ внутрь (кириллица и вложенность — fs.cpSync);
 *   2. docker build app/ -t <имя>;
 *   3. проверяет, что в образе модули на месте (docker run ... ls /app/modules).
 */
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const SRC = path.join(ROOT, 'modules');
const DST = path.join(ROOT, 'app', 'modules');
const DOCKER = fs.existsSync('C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe')
  ? 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
  : 'docker';

/** Запустить docker, вернуть {ok, out}. Никогда не бросает: вывод нужен для сообщения об ошибке. */
function docker(args, timeout = 0) {
  try {
    const out = execFileSync(DOCKER, args, { encoding: 'utf8', timeout, stdio: ['pipe', 'pipe', 'pipe'], maxBuffer: 64 * 1024 * 1024 });
    return { ok: true, out: (out || "").trim() };
  } catch (e) {
    return { ok: false, out: ((e.stdout || "") + (e.stderr || "")).trim() };
  }
}

/** Скопировать modules/ внутрь app/. Старую копию стираем: удалённый модуль не должен остаться в образе. */
function stage() {
  if (!fs.existsSync(SRC)) throw new Error("нет каталога " + SRC);
  fs.rmSync(DST, { recursive: true, force: true });
  fs.cpSync(SRC, DST, { recursive: true });
  const mods = fs.readdirSync(DST).filter((n) => !n.startsWith('.'));
  return mods;
}

function main() {
  const tag = process.argv[2] || "alyaeva-web:latest";
  const mods = stage();
  console.log("модули для образа:", mods.join(", "));
  // -q стоит ПОСЛЕ build: «docker -q build» — неизвестный флаг, и сборка не начиналась вовсе
  // (поймано первым прогоном скрипта).
  const b = docker(["build", "-q", "-t", tag, path.join(ROOT, "app")]);
  if (!b.ok) {
    console.error("сборка не удалась:\n" + b.out.slice(-2000));
    return 1;
  }
  console.log("образ собран:", tag, b.out.split(/\r?\n/).pop());
  // Проверка, а не надежда: модули должны быть в образе, иначе сайт не поднимется.
  const chk = docker(["run", "--rm", "--entrypoint", "sh", tag, "-c", "ls /app/modules"]);
  const missing = mods.filter((m) => chk.out.split(/\r?\n/).indexOf(m) < 0);
  if (!chk.ok || missing.length) {
    console.error("в образе нет модулей: " + (missing.join(", ") || chk.out.slice(-300)));
    return 1;
  }
  console.log("модули в образе на месте:", chk.out.split(/\r?\n/).join(", "));
  return 0;
}

process.exit(main());
