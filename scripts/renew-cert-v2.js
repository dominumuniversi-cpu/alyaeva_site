#!/usr/bin/env node
// Продление сертификата alyaeva.pro (Let's Encrypt, TLS-ALPN-01 через lego).
// Порт 80 перехвачен провайдером => только TLS-ALPN на 443.
// lego запускается с -p 443:443 (НЕ --network host: в Docker Desktop это не Windows-хост).
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

log('--- старт продления ---');
log('останавливаю proxy (lego нужен порт 443)');
docker(['stop', 'proxy'], 60000);

let exitCode = 1;
try {
  const r = docker(['run', '--rm', '--name', 'lego-alpn', '-p', '443:443',
    '-v', 'E:/DockerData/alyaeva/certbot/conf:/out', 'goacme/lego:latest',
    'run', '--accept-tos', '--email', EMAIL, '--path', '/out', '--tls', '-d', 'alyaeva.pro']);
  log('lego exit=' + r.status);
  if (r.stdout) log(r.stdout.trim().split('\n').slice(-3).join(' | '));
  if (r.stderr && r.status !== 0) log('stderr: ' + r.stderr.trim().split('\n').slice(-2).join(' | '));
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
    log('lego вернул ошибку — серт НЕ обновлён');
  }
} catch (e) {
  log('исключение: ' + e.message);
} finally {
  log('поднимаю proxy');
  docker(['start', 'proxy'], 60000);
  docker(['exec', 'proxy', 'nginx', '-s', 'reload'], 60000);
}
log('--- конец, exit=' + exitCode + ' ---');
process.exit(exitCode);