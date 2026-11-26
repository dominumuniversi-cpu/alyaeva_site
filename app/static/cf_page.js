/* Страница контент-завода: без перезагрузки.
   Всё, что меняет данные, идёт через fetch; страница остаётся на месте, а человек видит,
   что произошло, рядом с кнопкой, которую нажал.
   Разбор ответа — одна функция post(): иначе на каждой кнопке свой «если не ok»,
   и однажды один из них забывает показать причину. */
(function () {
  'use strict';

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  function say(el, text, kind) {
    if (!el) return;
    el.textContent = text || '';
    el.classList.remove('save-msg-ok', 'save-msg-bad');
    if (kind === 'ok') el.classList.add('save-msg-ok');
    if (kind === 'bad') el.classList.add('save-msg-bad');
  }

  /* Отправка формы. Данные — URLSearchParams: сервер ждёт form fields (как у «Интеграции»). */
  function post(url, data) {
    var b = new URLSearchParams();
    Object.keys(data || {}).forEach(function (k) { b.set(k, data[k] == null ? '' : data[k]); });
    return fetch(url, { method: 'POST', body: b, credentials: 'same-origin',
                        headers: { 'Accept': 'application/json' } })
      .then(function (r) {
        return r.json().catch(function () { return { ok: false, error: 'Ответ сервера не разобрать' }; });
      });
  }

  /* ---------- Материал ---------- */
  var body = $('#cfBody');
  var count = $('#cfCount');
  if (body) {
    body.addEventListener('input', function () {
      if (count) count.textContent = String(body.value.length);
      schedulePreview();
    });
  }
  var linksEl = $('#cfLinks');
  if (linksEl) linksEl.addEventListener('input', schedulePreview);

  function channels() {
    return $$('.cf-chan').filter(function (c) { return c.checked; }).map(function (c) { return c.value; });
  }

  /* Предпросмотр адаптации: пока человек печатает, он видит готовый текст каждой площадки.
     С задержкой: иначе один запрос на каждую букву. */
  var previewTimer = null, lastPreview = {};
  function schedulePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(runPreview, 700);
  }

  function runPreview() {
    if (!body || !body.value.trim()) return;
    post('/api/admin/cf/preview', {
      body: body.value, channels: channels().join(','), links: linksEl ? linksEl.value : '',
      media: ($('#cfMedia') || {}).value || ''
    }).then(function (d) {
      if (!d.ok) return;
      lastPreview = d.variants || {};
      var box = $('#cfPreview');
      if (box) renderPreview(box, lastPreview);
      if (Object.keys(lastPreview).length) {
        var hint = $('#cfPreviewHint');
        if (hint) hint.hidden = true;
      }
    });
  }

  function renderPreview(box, vars) {
    box.innerHTML = '';
    /* Площадок не выбрано — пустой блок без объяснения выглядит как поломка. Говорим словами. */
    if (!Object.keys(vars).length) {
      box.innerHTML = '<p class="cf-hint">Отметьте площадки — покажу, как текст ляжет в каждую.</p>';
      return;
    }
    Object.keys(vars).forEach(function (k) {
      var v = vars[k];
      var card = document.createElement('div');
      card.className = 'cf-prev';
      var meta = document.createElement('div');
      meta.className = 'cf-prev-head';
      meta.innerHTML = '<b>' + esc(v.name) + '</b> <span class="cf-prev-num">' +
        v.used + ' / ' + v.limit + '</span>' + (v.truncated ? ' <span class="cf-badge off">обрезано</span>' : '') +
        (v.ready ? '' : ' <span class="cf-badge off">площадка не подключена</span>') +
        (v.needs_media_now ? ' <span class="cf-badge off">нужно фото</span>' : '');
      var pre = document.createElement('div');
      pre.className = 'cf-prev-text';
      /* Текст показываем как есть, а разметку — тегами: человеку важно видеть, где будет жирный,
         а не читать <b>. marked-подобный просмотр не нужен: в тексте только b/i/s/a/code. */
      if (v.markup === 'html') pre.innerHTML = safeHtml(v.text); else pre.textContent = v.text;
      card.appendChild(meta);
      card.appendChild(pre);
      box.appendChild(card);
    });
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c];
    });
  }

  /* Разрешённые теги Telegram → те же теги в браузере. Всё остальное — текстом:
     мы не вставляем чужой HTML в страницу, даже если адаптер что-то пропустил. */
  function safeHtml(s) {
    return esc(s).replace(/&lt;(\/?(b|i|s|u|code|pre|blockquote|a))(&gt;|\s[^&]*&gt;)/g,
      function (m, slash, tag, rest) { return '<' + slash + tag + rest; });
  }

  /* ---------- Сохранить пост ----------
     Одна функция на две кнопки: сохранить и «сохранить и в очередь». Разница — только в
     времени отложенной публикации, поэтому дублировать проверки в двух местах незачем. */
  function savePost(when, list) {
    var msg = $('#cfMsg');
    var ch = channels();
    if (!body.value.trim()) { say(msg, 'Пустой пост публиковать некуда', 'bad'); return; }
    if (!ch.length) { say(msg, 'Отметьте хотя бы одну площадку', 'bad'); return; }
    /* Поле времени может быть пустым: тогда в очередь не ставим, а говорим, что сделать.
       Иначе «в очереди на  в » без времени — и человек не понимает, что произошло. */
    if (when && !String(($('#cfWhen') || {}).value || '').trim()) {
      say(msg, 'Выберите дату и время — или нажмите «Сохранить» без очереди', 'bad'); return;
    }
    var btns = $$('#cfSave, #cfSaveQueue');
    btns.forEach(function (b) { b.disabled = true; });
    say(msg, when ? 'Сохраняю и ставлю в очередь…' : 'Сохраняю…');
    post('/api/admin/cf/post', {
      title: ($('#cfTitle') || {}).value || '', body: body.value, channels: ch.join(','),
      media: ($('#cfMedia') || {}).value || '', links: ($('#cfLinks') || {}).value || '',
      publish_at: when ? (($('#cfWhen') || {}).value || '') : ''
    }).then(function (d) {
      if (!d.ok) { say(msg, d.error || 'Не сохранилось', 'bad'); return; }
      body.value = '';
      if ($('#cfTitle')) $('#cfTitle').value = '';
      if (count) count.textContent = '0';
      /* Время берём из ответа сервера — там уже то, что записано в базе. */
      say(msg, when
        ? 'Сохранено и в очереди на ' + whenText(String(d.publish_at || '')) + '. Уйдёт само.'
        : 'Сохранено. Тексты по площадкам готовы — откройте «Тексты по площадкам».', 'ok');
      addCard(d);
    }).catch(function () { say(msg, 'Сеть недоступна — попробуйте ещё раз', 'bad'); })
      .then(function () { btns.forEach(function (b) { b.disabled = false; }); });
  }

  /* «2026-11-27T09:00» → «27.11 в 09:00»: человеку так понятнее, чем ISO. */
  function whenText(v) {
    var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(v || ''));
    return m ? (m[3] + '.' + m[2] + ' в ' + m[4] + ':' + m[5]) : '';
  }

  var saveBtn = $('#cfSave');
  if (saveBtn) saveBtn.addEventListener('click', function () { savePost(false); });
  var queueBtn = $('#cfSaveQueue');
  if (queueBtn) queueBtn.addEventListener('click', function () { savePost(true); });

  function addCard(d) {
    var list = $('#cfList');
    if (!list) return;
    var el = document.createElement('article');
    el.className = 'post-card cf-post';
    el.dataset.post = d.id;
    el.innerHTML = '<div class="post-head"><span class="post-date">только что</span>' +
      '<span class="post-chans"><span class="chan">' + esc(d.channels_text) + '</span></span>' +
      '<span class="badge-status s-' + d.status + '">' + esc(d.status_name) + '</span></div>' +
      '<div class="post-body" data-open>' + esc(body.value || ($('#cfBody') ? '' : '')) + '</div>' +
      '<div class="post-foot"><div class="post-actions">' +
      '<button class="btn btn-ghost btn-sm" type="button" data-variants="' + d.id + '">Тексты по площадкам</button>' +
      '<button class="btn btn-ghost btn-sm" type="button" data-del="' + d.id + '">Удалить</button>' +
      '<button class="btn btn-ghost btn-sm" type="button" data-when="' + d.id + '">Отложить</button>' +
      '<button class="btn btn-sm" type="button" data-pub="' + d.id + '">Опубликовать</button></div>' +
      '<span class="save-msg"></span></div>' +
      '<div class="cf-variants" data-vp="' + d.id + '" hidden></div>';
    /* Текст карточки берём из вариантов: там его уже обрезала адаптация под площадку. */
    var first = d.variants && Object.keys(d.variants)[0];
    if (first) $('.post-body', el).textContent = d.variants[first].text;
    list.insertBefore(el, list.firstChild);
    bind(el);
  }

  /* ---------- Карточки: тексты по площадкам, удалить, опубликовать, копия ---------- */
  function bind(scope) {
    var root = scope || document;
    $$('[data-variants]', root).forEach(function (btn) {
      btn.addEventListener('click', function () { openVariants(btn); });
    });
    $$('[data-del]', root).forEach(function (btn) {
      btn.addEventListener('click', function () {
        var card = btn.closest('.cf-post'), msg = $('.save-msg', card);
        btn.disabled = true;
        post('/api/admin/cf/post/' + btn.dataset.del + '/delete', {}).then(function (d) {
          if (d.ok) { card.remove(); return; }
          say(msg, d.error || 'Не удалилось', 'bad');
          btn.disabled = false;
        }).catch(function () { btn.disabled = false; });
      });
    });
    $$('[data-copy]', root).forEach(function (btn) {
      btn.addEventListener('click', function () {
        var card = btn.closest('.cf-post'), msg = $('.save-msg', card);
        post('/api/admin/cf/post/' + btn.dataset.copy + '/copy', {}).then(function (d) {
          if (d.ok) { location.reload(); return; }
          say(msg, d.error || 'Не получилось', 'bad');
        });
      });
    });
    /* Отложить: спрашиваем время и ставим в очередь. Пустое — снимаем с очереди (останется черновиком). */
    $$('[data-when]', root).forEach(function (btn) {
      btn.addEventListener('click', function () {
        var card = btn.closest('.cf-post'), msg = $('.save-msg', card);
        var now = btn.dataset.at || ($('#cfWhen') ? $('#cfWhen').value : '');
        var when = window.prompt('Когда опубликовать? Формат: ГГГГ-ММ-ДДTЧЧ:ММ (пустое — снять с очереди)', now || '');
        if (when === null) return;
        post('/api/admin/cf/post/' + btn.dataset.when + '/schedule', { when: when.trim() }).then(function (d) {
          if (!d.ok) { say(msg, d.error || 'Не поставилось в очередь', 'bad'); return; }
          /* Ответ сервера — результат queue.schedule(): статус и время лежат внутри него. */
          if (d.when) { btn.dataset.at = d.when; } else { delete btn.dataset.at; }
          var badge = $('.badge-status', card);
          if (badge) {
            badge.textContent = d.when ? 'В очереди' : 'Черновик';
            badge.className = 'badge-status s-' + (d.when ? 'queued' : 'draft');
          }
          say(msg, d.when ? ('В очереди на ' + whenText(String(d.when)) + '. Уйдёт само.') : 'Снято с очереди: снова черновик.',
              d.when ? 'ok' : '');
        }).catch(function () { say(msg, 'Сеть недоступна', 'bad'); });
      });
    });

    $$('[data-pub]', root).forEach(function (btn) {
      btn.addEventListener('click', function () {
        var card = btn.closest('.cf-post'), msg = $('.save-msg', card);
        btn.disabled = true;
        say(msg, 'Публикую…');
        post('/api/admin/cf/post/' + btn.dataset.pub + '/publish', {}).then(function (d) {
          var badge = $('.badge-status', card);
          if (badge && d.status_name) { badge.textContent = d.status_name; badge.className = 'badge-status s-' + d.status; }
          say(msg, d.summary || d.error || 'Ничего не ушло', d.sent ? 'ok' : 'bad');
          if (!d.ok) btn.disabled = false;
          renderResults(card, d.result || {});
        }).catch(function () { say(msg, 'Сеть недоступна', 'bad'); btn.disabled = false; });
      });
    });
  }

  function renderResults(card, result) {
    var old = $('.cf-results', card);
    if (old) old.remove();
    var keys = Object.keys(result);
    if (!keys.length) return;
    var ul = document.createElement('ul');
    ul.className = 'cf-results';
    keys.forEach(function (k) {
      var v = String(result[k] || '');
      var li = document.createElement('li');
      li.className = v.indexOf('ok') === 0 ? 'ok' : (v.indexOf('skip') === 0 ? 'skip' : 'bad');
      li.innerHTML = '<b>' + esc(k) + '</b>: ' + esc(v.split(': ').slice(1).join(': ') || v);
      ul.appendChild(li);
    });
    var foot = $('.post-foot', card);
    if (foot) card.insertBefore(ul, foot);
  }

  /* Тексты по площадкам: то, что реально уйдёт в каждую. Правку человека сохраняем отдельно,
     чтобы она не терялась при следующем сохранении материала. */
  function openVariants(btn) {
    var card = btn.closest('.cf-post');
    var box = $('.cf-variants', card);
    if (!box) return;
    if (!box.hidden) { box.hidden = true; return; }
    var id = btn.dataset.variants;
    btn.disabled = true;
    say($('.save-msg', card), 'Загружаю тексты…');
    fetch('/api/admin/cf/post/' + id + '/variants.json', { credentials: 'same-origin',
      headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        btn.disabled = false;
        if (!d.ok) { say($('.save-msg', card), d.error || 'Не получилось', 'bad'); return; }
        renderVariants(box, d.variants || {}, id, card);
        box.hidden = false;
      })
      .catch(function () { btn.disabled = false; say($('.save-msg', card), 'Сеть недоступна', 'bad'); });
  }

  function renderVariants(box, vars, id, card) {
    box.innerHTML = '';
    Object.keys(vars).forEach(function (k) {
      var v = vars[k];
      var wrap = document.createElement('div');
      wrap.className = 'cf-var';
      var head = document.createElement('div');
      head.className = 'cf-var-head';
      head.innerHTML = '<b>' + esc(v.name || k) + '</b> <span class="cf-prev-num">' + (v.text || '').length +
        ' / ' + v.limit + '</span>' + (v.edited_by_human ? ' <span class="cf-badge info">правили вручную</span>' : '');
      var ta = document.createElement('textarea');
      ta.rows = 5;
      ta.value = v.text || '';
      ta.dataset.k = k;
      head.appendChild(ta);
      wrap.appendChild(head);
      box.appendChild(wrap);
    });
    var foot = document.createElement('div');
    foot.className = 'cf-actions';
    foot.innerHTML = '<button class="btn btn-sm" type="button">Сохранить правки</button>' +
      '<span class="save-msg"></span>';
    box.appendChild(foot);
    $('button', foot).addEventListener('click', function () {
      var data = {};
      $$('textarea[data-k]', box).forEach(function (t) { data[t.dataset.k] = t.value; });
      post('/api/admin/cf/post/' + id + '/variants', { texts: JSON.stringify(data) }).then(function (d) {
        say($('.save-msg', foot), d.ok ? 'Правки сохранены' : (d.error || 'Не сохранилось'), d.ok ? 'ok' : 'bad');
      });
    });
  }

  /* ---------- Сочинить (нейросеть) ---------- */
  var genBtn = $('#cfGenBtn');
  if (genBtn) genBtn.addEventListener('click', function () {
    var msg = $('#cfGenMsg'), topic = $('#cfTopic');
    if (!topic.value.trim()) { say(msg, 'Опишите, о чём пост', 'bad'); return; }
    genBtn.disabled = true;
    say(msg, 'Сочиняю… это может занять 10–20 секунд');
    post('/api/admin/cf/generate', { topic: topic.value, kind: ($('#cfTitle') || {}).value || '' })
      .then(function (d) {
        genBtn.disabled = false;
        if (!d.ok) { say(msg, d.error || 'Не получилось', 'bad'); return; }
        body.value = d.text;
        if (count) count.textContent = String(d.text.length);
        say(msg, 'Готово: ' + (d.provider || '') + ' ' + (d.model || ''), 'ok');
        lastPreview = d.variants || {};
        var box = $('#cfPreview');
        if (box) renderPreview(box, lastPreview);
        runPreview();
      })
      .catch(function () { genBtn.disabled = false; say(msg, 'Сеть недоступна', 'bad'); });
  });

  /* ---------- Площадки и нейросети: формами, но с ответом без перезагрузки ---------- */
  var tgt = $('#cfTargetsForm');
  if (tgt) tgt.addEventListener('submit', function (e) {
    e.preventDefault();
    var msg = $('#cfTgtMsg');
    /* Собираем ВСЕ поля формы: пустые тоже — сервер сам решает, что пустое значит «не менять». */
    var data = {};
    new FormData(tgt).forEach(function (v, k) { data[k] = v; });
    post(tgt.getAttribute('action'), data).then(function (d) {
      if (!d.ok) { say(msg, d.error || 'Не сохранилось', 'bad'); return; }
      say(msg, d.changed && d.changed.length ? 'Сохранено: ' + d.changed.length : 'Ничего не изменилось', 'ok');
      /* Значки площадок и «настроить» обновим перезагрузкой через секунду: список — серверный. */
      if (d.changed && d.changed.length) setTimeout(function () { location.reload(); }, 900);
    }).catch(function () { say(msg, 'Сеть недоступна', 'bad'); });
  });

  $$('.cf-llm').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var msg = $('#cfLLMMsg'), data = {};
      new FormData(form).forEach(function (v, k) { data[k] = v; });
      if (!data.enabled) data.enabled = '';
      post(form.getAttribute('action'), data).then(function (d) {
        if (!d.ok) { say(msg, d.error || 'Не сохранилось', 'bad'); return; }
        say(msg, d.enabled ? 'Подключено: ' + d.label + '. Кнопка «Сочинить» активна.' :
          (d.note || 'Сохранено'), d.enabled ? 'ok' : '');
        setTimeout(function () { location.reload(); }, 900);
      }).catch(function () { say(msg, 'Сеть недоступна', 'bad'); });
    });
  });

  $$('[data-llm-off]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var msg = $('#cfLLMMsg');
      post('/admin/posts/llm/' + btn.dataset.llmOff + '/off', {}).then(function (d) {
        if (!d.ok) { say(msg, d.error || 'Не отключилось', 'bad'); return; }
        say(msg, 'Отключено', 'ok');
        setTimeout(function () { location.reload(); }, 700);
      });
    });
  });

  /* ВСЕГДА: первый по порядку в ленте — самый свежий. Слушатели — на старые карточки,
     которые были в разметке до загрузки скрипта (новые вешает addCard). Без этой строки
     кнопки существующих постов не реагировали, и оставалось вечное «Загружаю тексты…». */
  bind(document);
})();
