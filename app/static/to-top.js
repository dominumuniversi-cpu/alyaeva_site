/* Кнопка «Наверх».
   Одна на весь сайт: главная (статика) и приложение (кабинет, админка) подключают
   этот же файл — копия в разметке разъезжается при первой правке (задача #226).
  
   Как подключить: на странице есть элемент с id="toTop", меню — скрипт внизу body. */
(function () {
  var tt = document.getElementById("toTop");
  if (!tt) return;

  /** Показать кнопку, когда страница прокручена, и спрятать наверху. */
  function show() {
    tt.classList.toggle("on", window.scrollY > 400);
  }
  window.addEventListener("scroll", show, { passive: true });
  show();

  /* Плавно наверх; если человек просил меньше движения — сразу. */
  tt.addEventListener("click", function (e) {
    var quiet = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    e.preventDefault();
    window.scrollTo({ top: 0, behavior: quiet ? "auto" : "smooth" });
  });
})();