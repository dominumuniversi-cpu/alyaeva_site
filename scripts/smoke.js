#!/usr/bin/env node
/**
 * smoke.js — открыть все страницы сайта и найти те, что отвечают 5xx.
 *
 * Зачем: 24.11.2026 кабинет отдавал 500 у любого клиента с заказом — шаблон звал
 * функцию, которой не было в контексте. Ни одна проверка этого не поймала.
 * Здесь ловим именно 5xx, а не «страница отдала 200».
 *
 *   node scripts\smoke.js                          # только гостевые страницы
 *   node scripts\smoke.js --user a@b.c --pass ПАРОЛЬ
 *   node scripts\smoke.js --user a@b.c --ask-pass   # пароль спросит, в историю не попадёт
 *
 * Код возврата: 0 — ни одной 5xx; 1 — есть 5xx или не удалось войти.
 */
const readline = require('readline');

const BASE = process.env.SMOKE_BASE || 'https://alyaeva.pro';
const HEAD = { 'X-DSH-Check': '1', 'User-Agent': 'alyaeva-smoke/1' };

/** Страницы без входа. */
const PUBLIC = ['/', '/ceny', '/policy', '/login', '/register'];

/** Страницы клиента после входа. */
const CABINET = ['/cabinet', '/cabinet/notifications', '/cabinet/projects', '/settings'];

/** Админка — точный список из кода: main.py, admin_clients.py, hooks.py. */
const ADMIN = ['/admin', '/admin/inbox', '/admin/products', '/admin/stats', '/admin/settings',
               '/admin/hooks', '/admin/clients', '/admin/photos'];

/** Спросить пароль, не показывая его на экране. */
function askPassword() {
  return new Promise((resolve) => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout, terminal: true });
    rl._writeToOutput = () => rl.output.write('');
    rl.question('Пароль: ', (answer) => { rl.close(); process.stdout.write(nl2); resolve(answer); });
  });
}
const nl2 = String.fromCharCode(10);

/** Значение аргумента командной строки. */
function arg(name, fallback) {
  const i = process.argv.indexOf('--' + name);
  return i > -1 ? process.argv[i + 1] : fallback;
}

/** Открыть адрес и вернуть код. */
async function get(url, cookie) {
  try {
    const h = Object.assign({}, HEAD, { Accept: 'text/html' });
    if (cookie) h.Cookie = cookie;
    const r = await fetch(BASE + url, { headers: h, redirect: 'manual' });
    return { code: r.status };
  } catch (e) { return { code: 0, error: e.message }; }
}

/** Войти формой и вернуть cookie. */
async function login(email, password) {
  const body = new URLSearchParams({ email: email, password: password }).toString();
  try {
    const h = Object.assign({}, HEAD, { Accept: 'text/html', 'Content-Type': 'application/x-www-form-urlencoded' });
    const r = await fetch(BASE + '/login', { method: 'POST', redirect: 'manual', headers: h, body: body });
    const jar = (r.headers.getSetCookie ? r.headers.getSetCookie() : []).map((c) => c.split(";")[0]);
    return { code: r.status, cookie: jar.join("; ") };
  } catch (e) { return { code: 0, cookie: '', error: e.message }; }
}

/** Пройти список адресов. */
async function sweep(list, cookie, label) {
  const rows = [];
  for (const url of list) {
    const r = await get(url, cookie);
    // 303 на /login и /register — это норма: вошедшего уводит дальше, а не ошибка.
    rows.push({ url: url, code: r.code, error: r.error });
    process.stdout.write('  ' + label.padEnd(8) + url.padEnd(26) + String(r.code) + nl2);
  }
  return rows;
}

/** Точка входа. */
(async () => {
  const email = arg('user');
  let cookie = "";
  if (email) {
    const password = process.argv.indexOf('--ask-pass') > -1 ? await askPassword() : arg('pass');
    const s = await login(email, password || '');
    process.stdout.write('  вход     ' + email + ' → ' + s.code + (s.cookie ? ' (сессия есть)' : ' (сессии НЕТ)') + nl2);
    if (!s.cookie) { process.stdout.write('Войти не удалось, дальше проверять нечего.' + nl2); process.exit(1); }
    cookie = s.cookie;
  }

  let rows = await sweep(PUBLIC, cookie, email ? 'вход' : 'гость');
  if (email) {
    rows = rows.concat(await sweep(CABINET, cookie, 'кабинет'));
    rows = rows.concat(await sweep(ADMIN, cookie, 'админка'));
  }

  const bad = rows.filter((r) => r.code === 0 || r.code >= 500);
  process.stdout.write(nl2 + 'страниц: ' + rows.length + ', 5xx или ошибок: ' + bad.length + nl2);
  for (const b of bad) process.stdout.write('  ПЛОХО ' + b.url + ' → ' + (b.error || b.code) + nl2);
  if (cookie) await get('/logout', cookie);
  process.exit(bad.length ? 1 : 0);
})();
