#!/usr/bin/env node
/**
 * Защищённый деплой приложения alyaeva-web.
 *
 * Порядок:
 *   1. Проверить, что сайт жив ДО начала (иначе не трогаем — чиним сначала).
 *   2. Поднять новый контейнер под временным именем, дождаться /health.
 *   3. Проверить живые страницы через временный контейнер.
 *   4. Только при успехе — заменить продовый контейнер.
 *   5. При любом сбое — удалить временный, прод не трогается.
 *
 * Запуск: node scripts/deploy-guarded.js <image>
 * Пример: node scripts/deploy-guarded.js alyaeva-web:abc1234
 */
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe';
const ENV_FILE = 'E:\\DockerData\\alyaeva\\.env';
const UPLOADS = 'E:/DockerData/alyaeva/uploads';
const PROD = 'alyaeva-web';
const CANDIDATE = 'alyaeva-web-candidate';

/** Запустить docker и вернуть вывод; ошибки не роняют процесс. */
function docker(args, opts = {}) {
  try {
    const out = execFileSync(DOCKER, args, { encoding: 'utf8', timeout: opts.timeout || 180000, stdio: ['pipe', 'pipe', 'pipe'] });
    return { ok: true, out: (out || '').trim() };
  } catch (e) {
    return { ok: false, out: ((e.stdout || '') + (e.stderr || '')).trim() };
  }
}

/** Прочитать .env и превратить в аргументы -e K=V. */
function envArgs() {
  const args = [];
  for (const line of fs.readFileSync(ENV_FILE, 'utf8').split(/\r?\n/)) {
    if (!line || line.startsWith('#') || !line.includes('=')) continue;
    args.push('-e', line);
  }
  return args;
}

/** Дождаться, пока контейнер ответит на /health. */
async function waitHealthy(name, timeoutMs = 60000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const r = docker(['exec', name, 'python', '-c',
      'import urllib.request,sys; sys.exit(0 if urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3).status==200 else 1)'], { timeout: 20000 });
    if (r.ok) return true;
    await new Promise((res) => setTimeout(res, 2000));
  }
  return false;
}

/**
 * Проверить, что живые страницы отдаются через контейнер.
 * @returns {Promise<boolean>}
 */
async function checkPages(name) {
  // Статика сайта живёт на alyaeva-site, поэтому "/" здесь не проверяется — только маршруты приложения.
  const pages = ['/health', '/login', '/api/products'];
  for (const p of pages) {
    const url = 'http://' + name + ':8000' + p;
    const r = docker(['run', '--rm', '--network', 'npc-proxy-net', 'curlimages/curl:latest',
      '-s', '-o', '/dev/null', '-w', '%{http_code}', '--max-time', '10', url], { timeout: 40000 });
    if (r.out !== '200') {
      console.error('  страница ' + p + ' -> ' + (r.out || 'нет ответа'));
      return false;
    }
    console.log('  ' + p + ' -> 200');
  }
  return true;
}

/** Поднять контейнер из образа под заданным именем. */
function startContainer(name, image) {
  docker(['rm', '-f', name]);
  const r = docker(['run', '-d', '--name', name, '--network', 'npc-proxy-net',
    ...envArgs(), '-v', UPLOADS + ':/data/uploads', image]);
  if (r.ok) docker(['network', 'connect', 'sc-stand-main_net', name]);
  return r.ok;
}

async function main() {
  const image = process.argv[2];
  if (!image) {
    console.error('Укажите образ: node scripts/deploy-guarded.js <image>');
    process.exit(2);
  }
  console.log('1. Проверка текущего прода...');
  const prodNow = docker(['ps', '--filter', 'name=^' + PROD + '$', '--format', '{{.Image}}']);
  if (!prodNow.ok || !prodNow.out) {
    console.error('   Прод не запущен — сначала восстановите его, деплой отменён.');
    process.exit(1);
  }
  console.log('   прод на образе: ' + prodNow.out);

  console.log('2. Подъём кандидата...');
  if (!startContainer(CANDIDATE, image)) {
    console.error('   Не удалось запустить кандидата. Прод не тронут.');
    process.exit(1);
  }
  const healthy = await waitHealthy(CANDIDATE);
  if (!healthy) {
    console.error('   Кандидат не прошёл healthcheck. Логи:');
    console.error(docker(['logs', '--tail', '30', CANDIDATE]).out);
    docker(['rm', '-f', CANDIDATE]);
    console.error('   Прод не тронут.');
    process.exit(1);
  }
  console.log('   healthcheck пройден');

  console.log('3. Проверка страниц кандидата...');
  if (!(await checkPages(CANDIDATE))) {
    docker(['rm', '-f', CANDIDATE]);
    console.error('   Страницы не отвечают. Прод не тронут.');
    process.exit(1);
  }

  console.log('4. Замена прода...');
  docker(['rm', '-f', PROD]);
  if (!startContainer(PROD, image)) {
    console.error('   КРИТИЧНО: не удалось поднять прод, откатываюсь...');
    startContainer(PROD, prodNow.out);
    docker(['rm', '-f', CANDIDATE]);
    process.exit(1);
  }
  if (!(await waitHealthy(PROD))) {
    console.error('   КРИТИЧНО: прод не отвечает, откатываюсь...');
    docker(['rm', '-f', PROD]);
    startContainer(PROD, prodNow.out);
    docker(['rm', '-f', CANDIDATE]);
    process.exit(1);
  }
  docker(['rm', '-f', CANDIDATE]);

  console.log('5. Живая проверка сайта...');
  const live = docker(['run', '--rm', 'curlimages/curl:latest', '-s', '--max-time', '20',
    '-o', '/dev/null', '-w', '%{http_code}', 'https://alyaeva.pro/'], { timeout: 60000 });
  if (live.out !== '200') {
    console.error('   Сайт не отвечает после деплоя — откатываюсь на ' + prodNow.out);
    docker(['rm', '-f', PROD]);
    startContainer(PROD, prodNow.out);
    process.exit(1);
  }
  console.log('   https://alyaeva.pro -> 200');
  console.log('\nГотово. Прод обновлён на ' + image);
}

main().catch((e) => {
  console.error('Ошибка деплоя:', e.message);
  docker(['rm', '-f', CANDIDATE]);
  process.exit(1);
});
