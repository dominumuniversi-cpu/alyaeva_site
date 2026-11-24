#!/usr/bin/env node
/**
 * preview.js — посмотреть кандидата ДО выката.
 *
 * Зачем: клон контейнера в сети прода бесполезен — в общем netns порт 8000 уже занят,
 * и проба читает ответ старого контейнера (задача #180, docs/mistakes.md).
 *
 * Что делает:
 *   1. собирает образ (по умолчанию alyaeva-preview:cand) из app/;
 *   2. поднимает его как app-cand в сети с базой, с .env проекта;
 *   3. ждёт "Application startup complete" в логах;
 *   4. гоняет проверки: страницы, CSS, отсутствие встроенных style;
 *   5. гасит кандидата и удаляет образ — за собой убирает всегда.
 *
 * Пример: node scripts\preview.js
 *         node scripts\preview.js --keep   (оставить кандидата для ручной проверки)
 */
const {spawnSync} = require('child_process');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const DOCKER = process.env.DOCKER_BIN || 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe';
const IMAGE = process.env.PREVIEW_IMAGE || 'alyaeva-preview:cand';
const NAME = process.env.PREVIEW_NAME || 'app-cand';
const NETWORK = process.env.PREVIEW_NETWORK || 'sc-stand-main_net';
const ENV_FILE = process.env.PREVIEW_ENV || 'E:/DockerData/alyaeva/.env';
const KEEP = process.argv.includes('--keep');

/** Запустить docker и вернуть stdout (и stderr, если merge) строкой. */
function sh(args, opts = {}) {
  const r = spawnSync(DOCKER, args, {
    encoding: 'utf8',
    timeout: opts.timeout || 600_000,
    maxBuffer: 32 * 1024 * 1024,
    env: {PATH: process.env.PATH, DOCKER_HOST: process.env.DOCKER_HOST || 'npipe:////./pipe/dockerDesktopLinuxEngine'},
  });
  const out = (r.stdout || '') + (opts.merge ? r.stderr || '' : '');
  if (r.error) throw r.error;
  if (r.status !== 0 && !opts.allowFail) {
    throw new Error(args[0] + ': ' + ((r.stderr || '').trim() || 'код ' + r.status));
  }
  return out;
}

/** Пауза без внешних процессов. */
function sleepSync(ms) { Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms); }

/** Снести контейнеры от прошлого прогона (образ не трогаем — он только что собран). */
function removeStale() {
  for (const name of [NAME, 'probe-cand']) {
    sh(['rm', '-f', name], {timeout: 60_000, allowFail: true});
  }
}

/** Ждать готовности приложения по логам контейнера. */
function waitReady(seconds = 60) {
  const until = Date.now() + seconds * 1000;
  while (Date.now() < until) {
    const logs = sh(['logs', '--tail', '40', NAME], {timeout: 30_000, merge: true}) || '';
    if (logs.includes('Application startup complete')) return true;
    if (logs.includes('address already in use')) throw new Error('порт 8000 занят — кандидат не поднялся');
    sleepSync(1000);
  }
  return false;
}

/** Проверить страницы кандидата из второго контейнера в той же сети. */
function check() {
  const probe = [
    'import http.client',
    'def get(path):',
    '    c = http.client.HTTPConnection("app-cand", 8000, timeout=20)',
    '    c.request("GET", path, headers={"Accept": "text/html", "X-DSH-Check": "1"})',
    '    r = c.getresponse(); return r.status, r.read().decode("utf-8", "replace")',
    'bad = 0',
    'for p in ("/health", "/ceny", "/policy", "/login", "/register", "/static/app.css", "/nope"):',
    '    st, b = get(p)',
    '    inline = b.count(chr(32) + chr(115) + chr(116) + chr(121) + chr(108) + chr(101) + chr(61) + chr(34))',
    '    want = 404 if p == "/nope" else 200',
    '    ok = st == want and (p == "/static/app.css" or inline == 0)',
    '    bad += 0 if ok else 1',
    '    print(("ок  " if ok else "ПЛОХО") + p, st, len(b), "style=", inline, flush=True)',
    'raise SystemExit(1 if bad else 0)',
  ].join('\n');
  const fs = require('fs');
  const tmp = path.join(ROOT, '_preview_probe.py');
  fs.writeFileSync(tmp, probe, 'utf8');
  try {
    const runId = sh(['run', '-d', '--name', 'probe-cand', '--network', NETWORK, '--entrypoint', 'python', IMAGE, '-c', 'import time; time.sleep(150)']).trim();
    sh(['cp', tmp, 'probe-cand:/_probe.py'], {timeout: 30_000});
    const out = sh(['exec', 'probe-cand', 'python', '-u', '/_probe.py'], {timeout: 120_000, allowFail: true}).trim();
    console.log(out);
    return out.length > 0 && !out.includes('ПЛОХО');
  } finally {
    sh(['rm', '-f', 'probe-cand'], {timeout: 60_000, allowFail: true});
    fs.unlinkSync(tmp);
  }
}

/** Основной поток: собрать, поднять, проверить, убрать. */
/** Убрать за собой: контейнеры и (если не --keep) образ. */
function cleanup() {
  removeStale();
  if (!KEEP) sh(['image', 'rm', '-f', IMAGE], {timeout: 120_000, allowFail: true});
}

function main() {
  console.log('1. Сборка образа', IMAGE);
  sh(['build', '-t', IMAGE, '-f', path.join(ROOT, 'app', 'Dockerfile'), path.join(ROOT, 'app')], {timeout: 900_000});
  if (!sh(['image', 'inspect', '--format', '{{.Id}}', IMAGE]).trim()) throw new Error('образ не собрался');
  removeStale();
  console.log('2. Кандидат', NAME, 'в сети', NETWORK);
  sh(['run', '-d', '--name', NAME, '--env-file', ENV_FILE, '--network', NETWORK, IMAGE], {timeout: 120_000});
  if (!waitReady()) throw new Error('кандидат не ответил за 60 секунд');
  console.log('3. Проверка страниц');
  const ok = check();
  console.log(ok ? 'Готово: кандидат отвечает и проходит проверки' : 'Есть проблемы — смотри таблицу выше');
  return ok ? 0 : 1;
}

const code = (() => {
  try { return main(); }
  catch (e) { console.error('Ошибка:', e.message); return 1; }
  finally { cleanup(); }
})();
process.exit(code);
