#!/usr/bin/env node
/**
 * Проверка стилей в живом браузере.
 *
 * «200 OK» на странице ничего не значит: /login год жил без CSS, потому что
 * /static/app.css отдавал 404. Смотреть надо не код ответа, а то, что
 * действительно применилось: загрузился ли лист, тот ли шрифт, те ли цвета.
 *
 *   node scripts/check-styles.js https://alyaeva.pro
 *
 * Выход 1 — если хоть одна страница без стилей (так это встаёт в CI).
 */
const puppeteer = require('puppeteer');

const BASE = (process.argv[2] || 'https://alyaeva.pro').replace(/\/$/, '');

// Страницы, которые обязан видеть каждый посетитель.
const PUBLIC = ['/', '/login', '/register', '/policy'];

// Страницы за авторизацией — проверяются, только если даны креды.
const ADMIN_EMAIL = process.env.ADMIN_EMAIL || '';
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || '';

// Сравнивать надо без учёта регистра: в CSS пишут #4B4690, браузер отдаёт #4b4690.
const TOKENS = { accent: '#4b4690', strong: '#2a2650' };

/** Открыть страницу и снять измерения: CSS, шрифт, токены. */
async function measure(page, path) {
  const resp = await page.goto(BASE + path, { waitUntil: 'networkidle2', timeout: 30000 });
  const status = resp ? resp.status() : 0;
  const data = await page.evaluate(() => {
    const cs = getComputedStyle(document.body);
    const root = getComputedStyle(document.documentElement);
    return {
      sheets: document.styleSheets.length,
      // Пустой лист (404 под видом css) тоже считается: у него нет правил.
      rules: Array.from(document.styleSheets).reduce((n, s) => {
        try { return n + s.cssRules.length; } catch (e) { return n; }
      }, 0),
      font: (cs.fontFamily || '').toLowerCase(),
      // Главная страница задаёт шрифт на корневом div, а не на body — смотрим оба.
      rootFont: (document.getElementById('top') ? getComputedStyle(document.getElementById('top')).fontFamily : '').toLowerCase(),
      accent: (root.getPropertyValue('--c-accent') || '').trim(),
      strong: (root.getPropertyValue('--c-strong') || '').trim(),
      bg: cs.backgroundColor,
    };
  });
  return { path, status, ...data };
}

/** Проверить одну страницу: вернуть список проблем. */
function problems(m) {
  const out = [];
  if (m.status !== 200) out.push('код ' + m.status);
  if (m.sheets === 0) out.push('ни один CSS не загрузился');
  else if (m.rules === 0) out.push('CSS загрузился пустым (' + m.sheets + ' листов, 0 правил)');
  const font = m.font.includes('nunito') ? m.font : m.rootFont;
  if (!font.includes('nunito')) out.push('шрифт = ' + (m.font || 'пусто') + ', а не Nunito');
  if (m.accent.toLowerCase() !== TOKENS.accent) out.push('--c-accent = ' + (m.accent || 'нет'));
  if (m.strong.toLowerCase() !== TOKENS.strong) out.push('--c-strong = ' + (m.strong || 'нет'));
  return out;
}

(async () => {
  const browser = await puppeteer.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 900 });
  const results = [];
  const errors = [];
  let loggedIn = false;

  for (const p of PUBLIC) {
    try {
      const m = await measure(page, p);
      const pr = problems(m);
      results.push({ p, pr });
      if (pr.length) errors.push(p + ': ' + pr.join('; '));
    } catch (e) {
      errors.push(p + ': не открылась — ' + e.message.split('\n')[0]);
    }
  }

  // Авторизованные страницы — только при наличии пароля.
  if (!ADMIN_EMAIL || !ADMIN_PASSWORD) {
    console.log('ПРОПУСК за авторизацией: не заданы ADMIN_EMAIL / ADMIN_PASSWORD — /cabinet и /admin не проверены');
  } else {
    try {
      await page.goto(BASE + '/login', { waitUntil: 'networkidle2' });
      await page.type('input[name="email"]', ADMIN_EMAIL);
      await page.type('input[name="password"]', ADMIN_PASSWORD);
      await Promise.all([
        page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 30000 }).catch(() => {}),
        page.click('button[type="submit"]'),
      ]);
      loggedIn = true;
      for (const p of ['/cabinet', '/admin', '/admin/settings', '/order']) {
        const m = await measure(page, p);
        const pr = problems(m);
        if (pr.length) errors.push(p + ': ' + pr.join('; '));
      }
    } catch (e) {
      errors.push('вход не удался — ' + e.message.split('\n')[0]);
    }
  }
  if (loggedIn) console.log('OK   /cabinet, /admin, /admin/settings, /order (под админом)');

  await browser.close();

  for (const r of results) {
    console.log((r.pr.length ? 'FAIL ' : 'OK   ') + r.p + (r.pr.length ? ' — ' + r.pr.join('; ') : ''));
  }
  if (ADMIN_EMAIL && ADMIN_PASSWORD && errors.length === 0 && !loggedIn) {
    errors.push('страницы за авторизацией не проверены — вход не удался');
  }
  if (errors.length) {
    console.log('\nПРОБЛЕМЫ (' + errors.length + '):');
    errors.forEach(e => console.log('  - ' + e));
    process.exit(1);
  }
  if (!ADMIN_EMAIL || !ADMIN_PASSWORD) {
    console.log('\nстили в порядке на публичных страницах. Страницы за входом НЕ проверены — добавьте ADMIN_EMAIL и ADMIN_PASSWORD');
    process.exit(0);
  }
  console.log('\nстили в порядке на всех проверенных страницах');
})();
