#!/usr/bin/env node
// Проверка живости alyaeva.pro. Запускать ПЕРЕД и ПОСЛЕ любых изменений.
// Node вместо PowerShell: PS 5.1 в этой среде не запускает внешние процессы.
const { spawnSync } = require('child_process');
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe';
const run = (args, t = 60000) => spawnSync(DOCKER, args, { encoding: 'utf8', timeout: t });

function curl(url, fmt) {
  const r = run(['run', '--rm', 'curlimages/curl:latest', '-s', '--max-time', '15', '-o', '/dev/null', '-w', fmt, url]);
  return (r.stdout || '').trim();
}
function status(name) {
  const r = run(['inspect', name, '--format', '{{.State.Status}}'], 20000);
  return (r.stdout || '').trim();
}

let ok = true;
const code = curl('https://alyaeva.pro/', '%{http_code}');
console.log('HTTPS / ->', code || '(нет ответа)');
if (code !== '200') ok = false;

const ssl = curl('https://alyaeva.pro/', '%{ssl_verify_result}');
console.log('SSL verify ->', ssl, ssl === '0' ? '(валиден)' : '(ПРОБЛЕМА)');
if (ssl !== '0') ok = false;

const site = status('alyaeva-site');
const proxy = status('proxy');
console.log('alyaeva-site=' + site, 'proxy=' + proxy);
if (site !== 'running' || proxy !== 'running') ok = false;

// Главная живёт отдельно от приложения. Если прод и репозиторий разошлись,
// правки теряются молча — поэтому это тоже повод для тревоги (#45).
try {
  const { execFileSync } = require('child_process');
  const out = execFileSync(process.execPath, [require('path').join(__dirname, 'deploy-site.js')],
                           { encoding: 'utf8', timeout: 60000 });
  const m = out.match(/изменится (\d+) из/);
  if (m) {
    console.log('ВНИМАНИЕ: ' + m[1] + ' файлов статики на проде отличаются от репозитория');
    console.log('  разложить: node scripts/deploy-site.js --apply');
  } else {
    console.log('статика: прод совпадает с репозиторием');
  }
} catch (e) {
  console.log('сверка статики не удалась: ' + e.message.split('\n')[0]);
}

console.log(ok ? 'OK: сайт жив' : 'ALARM: сайт недоступен');
process.exit(ok ? 0 : 1);
