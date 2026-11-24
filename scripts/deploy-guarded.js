#!/usr/bin/env node
/**
 * Защищённый деплой приложения alyaeva-web.
 *
 * Главный принцип: старый контейнер работает, пока новый не доказал, что жив.
 *
 * Схема (окно простоя — доли секунды, пока имя контейнера свободно):
 *   1. Проверить, что прод отвечает и .env читается — заранее.
 *   2. Поднять кандидата под временным именем; проверить его состояние и страницы по IP.
 *   3. Переключение: остановить старый, отвести его под `alyaeva-web-old`, занять имя кандидатом,
 *      подключить к сети прокси (при старте она не возвращается сама) и запустить.
 *   4. Дождаться, пока сайт оживёт снаружи по https (nginx минуту держит старый IP апстрима),
 *      и только после этого удалить старый контейнер.
 *   5. Не прошло — старый возвращается под своим именем и запускается.
 *
 * Запуск: node scripts/deploy-guarded.js <image>
 */
const { execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const DOCKER = fs.existsSync('C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe')
  ? 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe'
  : 'docker';
const ENV_FILE = 'E:\\DockerData\\alyaeva\\.env';
const UPLOADS = 'E:/DockerData/alyaeva/uploads';
const NET = 'npc-proxy-net';
const NET_DB = 'sc-stand-main_net';
const PROD = 'alyaeva-web';
const LOCK = path.join(os.tmpdir(), 'alyaeva-deploy.lock');

const PROD_NEW_NAME = 'alyaeva-web-old';
let candidateName = null;

/** Запустить docker, вернуть {ok, out}. Никогда не бросает. */
function docker(args, timeout = 180000) {
  try {
    const out = execFileSync(DOCKER, args, { encoding: 'utf8', timeout, stdio: ['pipe', 'pipe', 'pipe'] });
    return { ok: true, out: (out || '').trim() };
  } catch (e) {
    return { ok: false, out: ((e.stdout || '') + (e.stderr || '')).trim() };
  }
}

/** Прочитать .env в аргументы -e K=V. Бросает с понятным текстом, если файла нет. */
function envArgs() {
  if (!fs.existsSync(ENV_FILE)) throw new Error('Нет файла ' + ENV_FILE);
  const raw = fs.readFileSync(ENV_FILE, 'utf8').replace(/^\uFEFF/, '');
  const args = [];
  for (const line of raw.split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t.startsWith('#') || !t.includes('=')) continue;
    const i = t.indexOf('=');
    const key = t.slice(0, i).trim();
    const val = t.slice(i + 1).trim().replace(/^["']|["']$/g, '');
    if (key) args.push(key, val);
  }
  if (!args.length) throw new Error('.env пустой: ' + ENV_FILE);
  if (!args.includes('DB_PASSWORD')) throw new Error('В .env нет DB_PASSWORD');
  return args;
}

/** IP контейнера в сети приложения — по нему проверяем, не путаясь с DNS. */
function containerIp(name) {
  const r = docker(['inspect', name, '--format', '{{with index .NetworkSettings.Networks "' + NET + '"}}{{.IPAddress}}{{end}}']);
  return (r.out || '').trim();
}

/** Дождаться, что /health отвечает 200 (теперь 503 = БД лежит). */
async function waitHealthy(name, timeoutMs = 90000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const probe = 'import urllib.request,sys; r=urllib.request.urlopen("http://127.0.0.1:8000/health",timeout=3); sys.exit(0 if r.status==200 else 1)';
    const r = docker(['exec', name, 'python', '-c', probe], 20000);
    if (r.ok) return true;
    if (process.env.DEBUG_DEPLOY) console.error('   health: ' + (r.out || 'пусто').slice(0, 200));
    await new Promise((res) => setTimeout(res, 2000));
  }
  return false;
}

/** Проверить страницы приложения по IP. */
async function checkPages(ip) {
  for (const p of ['/health', '/login', '/api/products']) {
    const r = docker(['run', '--rm', '--network', NET, 'curlimages/curl:latest', '-s', '-o', '/dev/null',
      '-w', '%{http_code}', '--max-time', '10', 'http://' + ip + ':8000' + p], 40000);
    if (r.out !== '200') {
      console.error('  ' + p + ' -> ' + (r.out || 'нет ответа'));
      return false;
    }
    console.log('  ' + p + ' -> 200');
  }
  return true;
}

/** Поднять контейнер: имя, сетевой алиас, политика рестарта. */
function startContainer(name, image, alias) {
  const env = envArgs();
  const args = ['run', '-d', '--name', name, '--restart', 'unless-stopped', '--network', NET];
  if (alias) args.push('--network-alias', alias);
  for (let i = 0; i < env.length; i += 2) args.push('-e', env[i] + '=' + env[i + 1]);
  args.push('-v', UPLOADS + ':/data/uploads', image);
  const r = docker(args);
  if (r.ok) docker(['network', 'connect', NET_DB, name]);
  return r;
}

/** Снять алиас с контейнера, не убивая его. */
function detachAlias(name) {
  return docker(['network', 'disconnect', NET, name]);
}

/** Убрать кандидата и замок. */
function cleanup() {
  if (candidateName) docker(['rm', '-f', candidateName]);
  try { fs.unlinkSync(LOCK); } catch (e) { /* замка нет */ }
}

process.on('SIGINT', () => { console.error('\nПрервано — кандидат убран.'); cleanup(); process.exit(130); });
process.on('SIGTERM', () => { cleanup(); process.exit(143); });

async function main() {
  const image = process.argv[2];
  if (fs.existsSync(LOCK)) {
    /* Замок от прерванного прогона блокирует все следующие деплои навсегда.
       Держателя проверяем по pid: процесс мёртв — замок висячий, снимаем сами. */
    let holder = 0;
    try { holder = Number(fs.readFileSync(LOCK, 'utf8').trim()); } catch (e) { holder = 0; }
    let alive = false;
    if (holder > 0) {
      try { process.kill(holder, 0); alive = true; } catch (e) { alive = false; }
    }
    if (alive) {
      console.error('Деплой уже идёт (pid ' + holder + ', замок ' + LOCK + ').');
      process.exit(3);
    }
    console.log('Снимаю висячий замок от прерванного прогона (pid ' + holder + ').');
    try { fs.unlinkSync(LOCK); } catch (e) { /* уже нет */ }
  }
  if (!image) {
    console.error('Укажите образ: node scripts/deploy-guarded.js <image>');
    process.exit(2);
  }
  fs.writeFileSync(LOCK, String(process.pid));

  console.log('1. Проверка прода и окружения...');
  const prodImage = docker(['inspect', PROD, '--format', '{{.Config.Image}}']);
  if (!prodImage.ok || !prodImage.out) throw new Error('Прод не найден — сначала восстановите его');
  const prodUp = docker(['inspect', PROD, '--format', '{{.State.Status}}']).out;
  if (prodUp !== 'running') throw new Error('Прод в состоянии ' + prodUp + ', а не running');
  envArgs();
  fs.mkdirSync(UPLOADS, { recursive: true });
  fs.writeFileSync(path.join(os.tmpdir(), 'alyaeva-rollback-image.txt'), prodImage.out);
  console.log('   прод ' + prodImage.out + ' (' + prodUp + '), .env прочитан');

  console.log('2. Подъём кандидата...');
  candidateName = 'alyaeva-web-cand-' + Date.now();
  const cand = startContainer(candidateName, image, null);
  if (!cand.ok) throw new Error('Кандидат не запустился: ' + cand.out.slice(0, 200));
  if (!(await waitHealthy(candidateName))) {
    console.error(docker(['logs', '--tail', '20', candidateName]).out);
    throw new Error('Кандидат не прошёл healthcheck');
  }
  const ip = containerIp(candidateName);
  if (!ip) throw new Error('Не удалось определить IP кандидата');
  console.log('   healthcheck пройден, IP ' + ip);

  console.log('3. Проверка страниц кандидата...');
  if (!(await checkPages(ip))) throw new Error('Страницы кандидата не отвечают');

  console.log('4. Переключение (окно — доли секунды, пока имя занято)...');
  /* Имя контейнера в сети освобождает только ОСТАНОВКА — снятие алиаса его не отдаёт.
     Порядок: остановить прод → убрать его под запасное имя → занять имя прода кандидатом → запустить.
     Старый контейнер не удаляем до проверки нового: он и есть откат. `docker rename` применяется
     ТОЛЬКО к уже остановленному, поэтому зомби с --restart unless-stopped не появится. */
  docker(['rm', '-f', PROD_NEW_NAME]);
  const stopOld = docker(['stop', PROD]);
  if (!stopOld.ok) throw new Error('Не удалось остановить прод: ' + stopOld.out.slice(0, 200));
  const aside = docker(['rename', PROD, PROD_NEW_NAME]);
  if (!aside.ok) {
    docker(['start', PROD]);
    throw new Error('Не удалось отвести старый контейнер: ' + aside.out.slice(0, 200));
  }
  const promote = docker(['rename', candidateName, PROD]);
  if (!promote.ok) {
    docker(['rename', PROD_NEW_NAME, PROD]);
    docker(['start', PROD]);
    throw new Error('Не удалось занять имя прода: ' + promote.out.slice(0, 200));
  }
  candidateName = null;
  const started = docker(['start', PROD]);
  if (!started.ok) throw new Error('Новый прод не запустился: ' + started.out.slice(0, 200));
  /* Контейнер, отведённый в сторону, теряет сеть прокси — при `start` она НЕ возвращается.
     Без неё nginx отдаёт 502 (connect: Host is unreachable). Подключаем явно и ждём IP. */
  const attach = docker(['network', 'connect', NET, PROD]);
  if (!attach.ok && !/already exists|already connected/i.test(attach.out)) {
    throw new Error('Не удалось подключить прод к сети прокси: ' + attach.out.slice(0, 200));
  }
  for (let i = 0; i < 10; i += 1) {
    const nets = docker(['inspect', PROD, '--format', '{{json .NetworkSettings.Networks}}']);
    let ip = '';
    try { ip = (JSON.parse(nets.out || '{}')[NET] || {}).IPAddress || ''; } catch (e) { ip = ''; }
    if (ip) break;
    await new Promise((res) => setTimeout(res, 1000));
  }
  if (!(await waitHealthy(PROD))) {
    throw new Error('Новый прод не отвечает после переключения');
  }
  console.log('5. Живая проверка сайта...');
  /* 301 от прокси — норма: он всегда уводит http на https. Живой ответ — это 200 либо 301,
     а не только 200. Проверка «=== 200» уже роняла деплой на здоровом сайте (24.11). */
  const okLive = (code) => code === '200' || code === '301' || code === '302' || code === '303';
  let live = '';
  for (let i = 0; i < 6; i += 1) {
    live = docker(['run', '--rm', '--network', NET, 'curlimages/curl:latest', '-s', '--max-time', '10',
      '-o', '/dev/null', '-w', '%{http_code}', '-H', 'Host: alyaeva.pro', 'http://proxy/login'], 40000).out;
    if (okLive(live)) break;
    await new Promise((res) => setTimeout(res, 3000));
  }
  if (!okLive(live)) throw new Error('Приложение не отвечает через прокси после деплоя (код ' + (live || 'нет ответа') + ')');
  /* Настоящая проверка работы — снаружи по https. Но сразу после переключения контейнер может
     ещё не получить IP в сети приложения: docker подключает его с задержкой в секунды.
     Поэтому ждём до 30 секунд, а не падаем с первого 502. */
  const checkExternal = () => docker(['run', '--rm', 'curlimages/curl:latest', '-s', '--max-time', '15', '-o', '/dev/null',
    '-w', '%{http_code}', '-H', 'X-DSH-Check: 1', 'https://alyaeva.pro/login'], 60000).out;
  /* nginx кэширует IP апстрима (по умолчанию 30 с, но рвётся не строго по часам).
     Ждём до 90 секунд: переключение меняет IP контейнера, и старый IP мёртв. */
  let external = '';
  for (let i = 0; i < 30; i += 1) {
    external = checkExternal();
    if (external === '200') break;
    await new Promise((res) => setTimeout(res, 3000));
  }
  if (external !== '200') throw new Error('Снаружи по https /login отвечает ' + (external || 'нет ответа'));
  console.log('   через прокси ' + live + ', снаружи по https 200');

  console.log('5а. Новый жив — убираю старый контейнер.');
  docker(['rm', '-f', PROD_NEW_NAME]);
  console.log('\nГотово. Прод обновлён на ' + image);
  cleanup();
}

/** Вернуть прод в строй: алиас обязательно, иначе nginx отдаёт 502. */
function restoreProd() {
  // Прод мог не прижиться: тогда старый лежит под запасным именем и его надо вернуть.
  const prev = docker(['inspect', PROD_NEW_NAME, '--format', '{{.Name}}']);
  if (prev.ok && prev.out) {
    docker(['rm', '-f', PROD]);
    const back = docker(['rename', PROD_NEW_NAME, PROD]);
    if (back.ok) docker(['start', PROD]);
  }
  // Алиас ставим ВСЕГДА — без него прокси не найдёт контейнер по имени.
  docker(['network', 'connect', NET, '--alias', PROD, PROD]);
  const check = docker(['network', 'inspect', NET, '--format', '{{json .Containers}}']);
  return check.ok;
}
main().catch((e) => {
  console.error('ОШИБКА ДЕПЛОЯ:', e.message);
  console.error('   Восстанавливаю прод...');
  restoreProd();
  cleanup();
  const code = docker(['run', '--rm', '--network', NET, 'curlimages/curl:latest', '-s', '--max-time', '10',
    '-o', '/dev/null', '-w', '%{http_code}', '-H', 'Host: alyaeva.pro', 'http://proxy/login'], 40000).out;
  console.error('   проверка после восстановления: /login -> ' + (code || 'нет ответа'));
  process.exit(code === '200' || code === '301' ? 0 : 1);
});
