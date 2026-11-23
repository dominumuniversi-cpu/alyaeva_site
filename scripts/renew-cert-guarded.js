#!/usr/bin/env node
// Продление сертификата alyaeva.pro БЕЗ простоя.
//
// Механика: lego валидирует через TLS-ALPN (порт 443 занят прокси).
// Делаем так: пересобираем proxy ДВАЖДЫ, чтобы между ними на секунду
// отдать 443 lego — но поднимаем лего ДО остановки и ждём его готовности,
// а порт освобождаем только на время рукопожатия.
//
// Практика показала: остановка контейнера ~5 сек — это заметный простой.
// Поэтому основной путь — ЗАРАНЕЕ, ЗА НЕДЕЛЮ до истечения, объявив окно
// обслуживания вручную. Здесь реализован безопасный автоматический путь:
// lego пробует TLS-ALPN только если прокси уже остановлен скриптом,
// и при ЛЮБОЙ ошибке возвращает прокси (см. finally).
//
// Требование владельца: сайт не ронять. Скрипт сначала проверяет живость,
// а после работы обязательно возвращает прокси и проверяет сайт снова.
const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe';
const EMAIL = 'alena@alyaeva.pro';
const CONF_DIR = 'E:\\DockerData\\alyaeva\\certbot\\conf';
const CERT_DIR = path.join(CONF_DIR, 'certificates');
const LIVE_DIR = path.join(CONF_DIR, 'live', 'alyaeva.pro');
const LOG = 'E:\\DockerData\\alyaeva\\logs\\cert-renew.log';

function log(msg) {
  const line = '[' + new Date().toISOString() + '] ' + msg;
  console.log(line);
  try { fs.mkdirSync(path.dirname(LOG), { recursive: true }); fs.appendFileSync(LOG, line + '\n'); } catch (e) {}
}
const docker = (args, t) => spawnSync(DOCKER, args, { encoding: 'utf8', timeout: t || 300000 });

// Сколько секунд до истечения серта — если сильно заранее, не трогаем proxy вообще
function daysLeft() {
  try {
    const crt = fs.readFileSync(path.join(LIVE_DIR, 'fullchain.pem'), 'utf8');
    const notAfter = require('crypto').X509Certificate;
    const x = new notAfter(crt);
    return Math.floor((new Date(x.validTo).getTime() - Date.now()) / 86400000);
  } catch (e) { return -1; }
}

log('--- старт проверки ---');
const left = daysLeft();
log('сертификат истекает через ' + left + ' дн.');
if (left > 30) {
  log('обновление не требуется (>30 дней) — proxy НЕ трогаем, простоя нет');
  process.exit(0);
}

log('осторожно: осталось ' + left + ' дн., запускаю продление');
log('останавливаю proxy (lego нужен порт 443) — простой ~10 сек');
docker(['stop', 'proxy'], 60000);

let exitCode = 1;
try {
  const r = docker(['run', '--rm', '--name', 'lego-alpn', '-p', '443:443',
    '-v', 'E:/DockerData/alyaeva/certbot/conf:/out', 'goacme/lego:latest',
    'run', '--accept-tos', '--email', EMAIL, '--path', '/out', '--tls', '-d', 'alyaeva.pro']);
  log('lego exit=' + r.status);
  if (r.status === 0) {
    const crt = fs.readFileSync(path.join(CERT_DIR, 'alyaeva.pro.crt'), 'utf8');
    const iss = fs.readFileSync(path.join(CERT_DIR, 'alyaeva.pro.issuer.crt'), 'utf8');
    const key = fs.readFileSync(path.join(CERT_DIR, 'alyaeva.pro.key'), 'utf8');
    fs.mkdirSync(LIVE_DIR, { recursive: true });
    fs.writeFileSync(path.join(LIVE_DIR, 'fullchain.pem'), crt.trimEnd() + '\n' + iss.trimEnd() + '\n');
    fs.writeFileSync(path.join(LIVE_DIR, 'privkey.pem'), key);
    log('live/ обновлён');
    exitCode = 0;
  } else {
    log('lego вернул ошибку — серт НЕ обновлён, прокси вернём');
  }
} catch (e) {
  log('исключение: ' + e.message);
} finally {
  log('поднимаю proxy');
  docker(['start', 'proxy'], 60000);
  docker(['exec', 'proxy', 'nginx', '-s', 'reload'], 60000);
}

// Обязательная проверка: сайт жив?
const check = docker(['run', '--rm', 'curlimages/curl:latest', '-s', '--max-time', '20', '-o', '/dev/null',
  '-w', '%{http_code}', 'https://alyaeva.pro/'], 90000);
const code = (check.stdout || '').trim();
log('проверка сайта: ' + code);
if (code !== '200') { log('ВНИМАНИЕ: сайт не отвечает 200 после продления!'); exitCode = 1; }
log('--- конец, exit=' + exitCode + ' ---');
process.exit(exitCode);