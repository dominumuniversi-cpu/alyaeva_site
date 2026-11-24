/* Сохранение формы без перезагрузки страницы.
   Один файл на все формы админки: иначе одинаковый код живёт в двух шаблонах и
   расходится при первой же правке (нашёл дубль 24.11.2026, задачи #215 и #217).

   Как подключить:
     <form id="f_x" method="post" action="…">
     <span class="save-msg" id="f_x_msg" role="status" aria-live="polite"></span>
     <script src="/static/form-save.js" data-form="f_x"></script>

   Без JS форма отправляется обычным POST — поведение сервера прежнее. */
(function () {
  var tag = document.currentScript;
  var formId = (tag && tag.dataset.form) || "";
  var form = document.getElementById(formId);
  var msg = document.getElementById(formId + "_msg");
  if (!form || !msg || !window.fetch) return;

  /** Показать сообщение нужного вида рядом с кнопкой. */
  function say(text, kind) {
    msg.textContent = text;
    msg.className = "save-msg" + (kind ? " save-msg-" + kind : "");
  }

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var btn = form.querySelector("button[type=submit]");
    if (btn) btn.disabled = true;
    say("Сохраняю…");
    fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      credentials: "same-origin",
      headers: {"Accept": "application/json", "X-Requested-With": "fetch"}
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.ok) {
          say(d.saved ? ("Сохранено: " + d.saved) : "Сохранено", "ok");
        } else {
          say((d && d.error) || "Не сохранилось — попробуйте ещё раз", "bad");
        }
      })
      .catch(function () { say("Сеть недоступна — попробуйте ещё раз", "bad"); })
      .then(function () { if (btn) btn.disabled = false; });
  });
})();