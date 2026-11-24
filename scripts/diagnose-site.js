#!/usr/bin/env node
/**
 * Диагностика «сайт не работает» по слоям: от контейнера до браузера.
 * Показывает, на каком слое обрыв, а не просто «упало».
 *
 * Запуск: node scripts/diagnose-site.js
 */
const { execFileSync } = require('child_process');
const fs = require('fs');

const DOCKER = fs.existsSync('C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe')
  ? 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
  : 'docker';
const NET = 'npc-proxy-net';

/** Запустить docker без падения. */
function docker(args, timeout = 60000) {
  try {
    return execFileSync(DOCKER, args, { encoding: 'utf8', timeout, stdio: ['pipe', 'pipe', 'pipe'] }).trim();
  } catch (e) {
    return ((e.stdout || '') + (e.stderr || '')).trim();
  }
}

/** Напечатать строку проверки. */
function step(name, ok, detail) {
  console.log((ok ? '  OK   ' : '  СБОЙ ') + name + (detail ? ' — ' + detail : ''));
  return ok;
}

let failed = 0;
const check = (name, ok, detail) => { if (!step(name, ok, detail)) failed += 1; };
const code = (url) => docker(['run', '--rm', '--network', NET, 'curlimages/curl:latest', '-s', '-o', '/dev/null',
  '-w', '%{http_code}', '--max-time', '10', url], 40000);

console.log('1. Контейнеры');
for (const name of ['proxy', 'alyaeva-site', 'alyaeva-web']) {
  const st = docker(['inspect', name, '--format', '{{.State.Status}}']);
  check(name, st === 'running', st || 'не найден');
}

console.log('\n2. Сеть и DNS');
const dns = docker(['run', '--rm', '--network', NET, 'alpine', 'sh', '-c', 'getent hosts alyaeva-web || echo FAIL'], 40000);
check('name alyaeva-web резолвится', !dns.includes('FAIL'), dns.split(/\s+/)[0] || 'нет');
const nets = docker(['inspect', 'alyaeva-web', '--format', '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}']);
check('в сети ' + NET, nets.includes(NET), nets.trim());

console.log('\n3. Приложение (внутри сети, без TLS)');
check('/health', code('http://alyaeva-web:8000/health') === '200', '');
check('/login', code('http://alyaeva-web:8000/login') === '200', '');

console.log('\n4. Прокси (маршрутизация по Host)');
check('/login через proxy', code('http://proxy/login') === '301', '301 = редирект на https, норма');
const tls = docker(['run', '--rm', '--network', NET, 'curlimages/curl:latest', '-sk', '-s', '-o', '/dev/null',
  '-w', '%{http_code}', '--max-time', '10', '-H', 'Host: alyaeva.pro', 'https://proxy/login'], 40000);
check('/login через https', tls === '200', tls === '000' ? 'сертификат не для имени proxy — это норма' : tls);

console.log('\n5. Сайт снаружи');
const out = docker(['run', '--rm', 'curlimages/curl:latest', '-s', '--max-time', '20', '-o', '/dev/null',
  '-w', '%{http_code} ssl=%{ssl_verify_result}', 'https://alyaeva.pro/'], 60000);
check('https://alyaeva.pro', out.startsWith('200'), out);

console.log('\n' + (failed ? failed + ' проверок не прошло' : 'всё в порядке'));
process.exit(failed ? 1 : 0);
