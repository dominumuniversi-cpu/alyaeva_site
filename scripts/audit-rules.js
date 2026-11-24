/**
 * Аудит правил проекта: размеры файлов, дубли, битые ссылки на разделы.
 * Каждые пять закрытых задач — прогонять. Не зависит от памяти агента.
 *
 * Запуск: node scripts/audit-rules.js
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const LIMIT = 32000;   // бюджет манифеста проекта, байты
const BUDGET = 65536;  // бюджет всех инструкций вместе
const GLOBAL = 28533;  // глобальный $DSH_HOME/AGENTS.md

/** Прочитать файл правил; null, если файла нет. */
function read(rel) {
  const p = path.join(ROOT, rel);
  return fs.existsSync(p) ? fs.readFileSync(p, 'utf8') : null;
}

/** Найти дословные повторы заголовков разделов. */
function duplicateHeadings(text) {
  const heads = (text.match(/^## .+$/gm) || []).map(function (h) { return h.replace(/^## /, '').trim(); });
  return heads.filter(function (h, i) { return heads.indexOf(h) !== i; });
}

/**
 * Ссылки на разделы подробных правил: «раздел «ИМЯ»» или `docs/x.md», «ИМЯ»».
 * Кавычки вокруг цитат владельца и заголовки самого манифеста ссылками не считаются.
 */
function brokenRefs(manifest, docsText) {
  const refs = [];
  const patterns = [
    /раздел\s+«([^»]{4,60})»/g,
    /`docs\/[a-z-]+\.md`,\s*«([^»]{4,60})»/g,
  ];
  patterns.forEach(function (re) {
    let m;
    while ((m = re.exec(manifest))) refs.push(m[1]);
  });
  const uniq = refs.filter(function (r, i) { return refs.indexOf(r) === i; });
  return uniq.filter(function (r) { return docsText.indexOf(r) === -1; });
}
/** Дословные повторы абзацев между манифестом и подробными правилами. */
function duplicatedParagraphs(manifest, docsText) {
  const norm = function (t) { return t.replace(/\s+/g, ' ').toLowerCase(); };
  const shown = norm(docsText);
  return manifest.split(/\n\n+/).filter(function (p) {
    return p.length > 200 && shown.indexOf(norm(p).slice(-90)) !== -1;
  });
}

const manifest = read('AGENTS.md') || '';
const rules = read(path.join('docs', 'rules.md')) || '';
const mistakes = read(path.join('docs', 'mistakes.md')) || '';
const otherDocs = [read(path.join('docs', 'deploy.md')), read(path.join('docs', 'order-flow.md')), mistakes].filter(Boolean).join('\n');
const size = Buffer.byteLength(manifest);

console.log('AGENTS.md:', size, 'байт |', size <= LIMIT ? 'в бюджете, запас ' + (LIMIT - size) : 'ПЕРЕБОР на ' + (size - LIMIT));
const left = BUDGET - size - GLOBAL;
console.log('общий бюджет инструкций:', left >= 0 ? 'запас ' + left : 'ПЕРЕБОР ' + (-left));
const dupHeads = duplicateHeadings(rules);
console.log('дубли заголовков в rules.md:', dupHeads.length ? dupHeads.join(' | ') : 'нет');
const nums = (mistakes.match(/^## (\d+)\./gm) || []).map(function (x) { return x.replace(/^## /, '').replace('.', ''); });
const dupNums = nums.filter(function (n, i) { return nums.indexOf(n) !== i; });
console.log('ошибок в mistakes.md:', nums.length, '| повторы номеров:', dupNums.length ? dupNums.join(',') : 'нет');
const broken = brokenRefs(manifest, rules + otherDocs);
console.log('ссылок на несуществующие разделы:', broken.length ? broken.join(' | ') : 'нет');
const dupParas = duplicatedParagraphs(manifest, rules);
console.log('дословных повторов абзацев с docs/rules.md:', dupParas.length);

const bad = size > LIMIT || left < 0 || dupHeads.length > 0 || dupNums.length > 0 || broken.length > 0 || dupParas.length > 0;
console.log(bad ? 'ИТОГ: есть что поправить' : 'ИТОГ: правила чистые');
process.exit(bad ? 1 : 0);
