#!/usr/bin/env node
/**
 * Деплой статической части сайта: build/dist → E:\DockerData\alyaeva\html
 *
 * Почему скрипт, а не копирование руками: главная страница год жила вне репозитория,
 * правки на проде не попадали в git, правки в git не попадали на прод (#45).
 * Теперь единственный источник — build/dist в репозитории.
 *
 *   node scripts/deploy-site.js            # что изменится (ничего не трогает)
 *   node scripts/deploy-site.js --apply    # разложить файлы и проверить сайт
 */
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const SRC = path.join(ROOT, 'build', 'dist');
const DST = process.env.SITE_ROOT || 'E:\\DockerData\\alyaeva\\html';
const APPLY = process.argv.includes('--apply');
const DOCKER = 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe';
const CONTAINER = 'alyaeva-site';

/** Собрать список файлов каталога с относительными путями. */
function walk(dir, base = '') {
  const out = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const rel = base ? base + '/' + e.name : e.name;
    if (e.isDirectory()) out.push(...walk(path.join(dir, e.name), rel));
    else out.push(rel);
  }
  return out;
}

/** Сравнить два файла по содержимому. */
function same(a, b) {
  if (!fs.existsSync(b)) return false;
  const x = fs.readFileSync(a), y = fs.readFileSync(b);
  return x.length === y.length && x.equals(y);
}

function main() {
  if (!fs.existsSync(SRC)) {
    console.error('нет каталога ' + SRC);
    process.exit(1);
  }
  const files = walk(SRC);
  const changed = files.filter(f => !same(path.join(SRC, f), path.join(DST, f)));

  if (!changed.length) {
    console.log('прод совпадает с репозиторием — деплоить нечего (' + files.length + ' файлов)');
    return;
  }

  console.log('изменится ' + changed.length + ' из ' + files.length + ':');
  changed.forEach(f => console.log('  ' + f));

  if (!APPLY) {
    console.log('\nэто пробный прогон. Чтобы разложить файлы: node scripts/deploy-site.js --apply');
    return;
  }

  for (const f of changed) {
    const dst = path.join(DST, f);
    fs.mkdirSync(path.dirname(dst), { recursive: true });
    fs.copyFileSync(path.join(SRC, f), dst);
  }
  console.log('\nразложено: ' + changed.length);

  // nginx раздаёт статику из смонтированного каталога, но перезапуск дешёвый (2-3 сек) и безопасный.
  try {
    execFileSync(DOCKER, ['restart', CONTAINER], { stdio: 'ignore' });
    console.log('контейнер ' + CONTAINER + ' перезапущен');
  } catch (e) {
    console.log('перезапуск не удался: ' + e.message.split('\n')[0]);
  }
}

main();
