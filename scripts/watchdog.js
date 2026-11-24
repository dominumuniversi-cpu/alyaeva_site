#!/usr/bin/env node
/**
 * Сторож: проверяет живой сайт и приложение.
 * Запускается по расписанию. При падении — пишет тревогу в лог и возвращает код 1.
 *
 * Проверка сделана на СВЕЖЕМ контейнере без кэша — иначе Docker отдаёт
 * закэшированный ответ и сторож молчит, когда сайт на самом деле лежит.
 */
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe';
const LOG = 'E:\\DockerData\\alyaeva\\logs\\watchdog.log';

/** Запустить docker, вернуть вывод без падения. */
function docker(args) {
  try {
    return execFileSync(DOCKER, args, { encoding: 'utf8', timeout: 60000, stdio: ['pipe', 'pipe', 'pipe'] }).trim();
  } catch (e) {
    return '';
  }
}

/** Записать строку в лог сторожа. */
function log(line) {
  fs.mkdirSync(path.dirname(LOG), { recursive: true });
  fs.appendFileSync(LOG, new Date().toISOString() + ' ' + line + '\n');
}

const problems = [];

// 1. Сайт снаружи (свежий контейнер, публичный DNS)
const web = docker(['run', '--rm', '--dns', '8.8.8.8', 'curlimages/curl:latest',
  '-s', '--max-time', '20', '-o', '/dev/null', '-w', '%{http_code}', 'https://alyaeva.pro/']);
if (web !== '200') problems.push('сайт снаружи: ' + (web || 'нет ответа'));

// 2. Приложение
const app = docker(['run', '--rm', '--network', 'npc-proxy-net', 'curlimages/curl:latest',
  '-s', '--max-time', '10', '-o', '/dev/null', '-w', '%{http_code}', 'http://alyaeva-web:8000/health']);
if (app !== '200') problems.push('приложение /health: ' + (app || 'нет ответа'));

// 3. Контейнеры должны быть запущены
for (const name of ['proxy', 'alyaeva-site', 'alyaeva-web']) {
  const st = docker(['ps', '--filter', 'name=^' + name + '$', '--format', '{{.Status}}']);
  if (!st.startsWith('Up')) problems.push('контейнер ' + name + ': ' + (st || 'не найден'));
}

// 4. Зомби-контейнеры (они перезапускаются вечно и мешают)
const zombies = docker(['ps', '-a', '--filter', 'status=restarting', '--format', '{{.Names}}']);
if (zombies) problems.push('перезапускаются: ' + zombies.replace(/\s+/g, ' '));

// 5. Мусор от проверок
const junk = docker(['ps', '-a', '--filter', 'status=created', '--format', '{{.Names}}']);
if (junk) problems.push('мусорные контейнеры: ' + junk.replace(/\s+/g, ' '));

if (problems.length) {
  log('ПРОБЛЕМА: ' + problems.join('; '));
  console.error('Тревога:\n - ' + problems.join('\n - '));
  process.exit(1);
}
log('ok');
console.log('сайт и приложение в порядке');
