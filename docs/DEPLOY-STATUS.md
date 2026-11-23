# Состояние развёртывания alyaeva.pro

**Дата:** 23.11.2026 · **Сервер:** DIMAS 192.168.8.49 · **Контейнеры:** `alyaeva-site` (внутренний), `proxy` (443/80 наружу)

## Что работает
| Проверка | Результат |
|---|---|
| Сайт через прокси (SNI alyaeva.pro) | 200, ~0.01 c |
| index.html | 200, 27 KB (gzip) |
| CSS / шрифты / WebP | 200, кэш 365 дней |
| 404 | кастомная страница, 404 |
| robots.txt, sitemap.xml | 200 |
| Заголовки безопасности | HSTS, CSP, nosniff, X-Frame, Referrer, Permissions |
| API контейнера наружу | не публикуется (только через proxy) |

## Размеры
- CSS 3.6 KB · HTML 28 KB (gzip ~7 KB) · WebP-обложка 330 KB (было PNG 4.9 MB) · шрифты 100 KB локально.
- Общий вес страницы с картинками и видео ≈ 9.8 MB (видео 4.1 MB — грузится по клику, preload=metadata).

## ЧТО НЕ ЗАВЕРШЕНО — SSL (блокер на вашей стороне)
Let's Encrypt дважды вернул **timeout** (HTTP-01 и TLS-ALPN-01) на 80 и 443:
- внешний IP 188.226.71.169 — это **WAN-шлюз провайдера** (трейс: 192.168.8.156 → 188.226.88.1 → …);
- на машине 192.168.8.49 песочницы нет — трафик снаружи до неё **не доходит**;
- запрос на публичный IP с 80 получает **403 «insufficient security level»** от устройства, которое отдаёт **«Server: Web server»** — это не nginx (наш прокси отдаёт nginx/1.31.9).

**Сейчас сайт использует самоподписанный сертификат** (заглушка, 1 день) — браузер покажет предупреждение.

### Вариант A — проброс (рекомендуется)
1. В роутере (управление у провайдера/арендодателя сети): проброс **80→192.168.8.49:80** и **443→192.168.8.49:443**.
2. Сказать мне — я повторю выпуск:
   ```
   docker stop proxy
   docker run --rm --network host -v E:/DockerData/alyaeva/certbot/conf:/out goacme/lego:latest ^
     run --accept-tos --email alena@alyaeva.pro --path /out --tls -d alyaeva.pro
   docker start proxy
   ```
3. Сертификат ляжет в `certbot\conf\certificates\alyaeva.pro.{crt,key}`, vhost обновится на него.
   Продление — ежедневная задача; см. `scripts\renew-cert.ps1`.

### Вариант B — DNS-01 через reg.ru (без открытых портов)
Нужен **API-токен reg.ru**: ваш кабинет → «API и интеграции» → «API-ключи».
Тогда я выпущу сертификат по DNS-записи, порты не понадобятся вовсе.

### Вариант C — купленный сертификат
Пришлите `fullchain.pem` + `privkey.pem` в `certbot\conf\live\alyaeva.pro\` — переключу vhost.

## Файлы
- Сайт: `E:\DockerData\alyaeva\html\` (собранная версия)
- Исходники сборки: `E:\!Develop\sites\alyaeva\build\dist`
- Vhost: `E:\DockerData\nginx\vhosts\alyaeva.pro.conf`
- Конфиг сайта: `E:\DockerData\alyaeva\nginx-site\default.conf`
- Бэкапы прокси: `E:\DockerData\nginx\*.bak-2026-11-23`
