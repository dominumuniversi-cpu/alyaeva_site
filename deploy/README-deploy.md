# Деплой alyaeva.pro (внешний IP 188.226.71.169, DNS уже указывает)

## Что где
- Сайт: `alyaeva/site/index.html` (самодостаточная выгрузка; лучше пересобрать по манифестам `../manifests/`).
- Nginx-хост: `E:\DockerData\nginx\conf.d\alyaeva.pro.conf` (этот каталог — общий прокси сервера).
- Сертификаты: `E:\DockerData\nginx\certs\alyaeva.pro\`.

## Шаги
1. На DNS-хостинге домена создать A-записи: `@ → 188.226.71.169` и `www → 188.226.71.169` (уже сделано — проверить).
2. Положить `conf.d\00-rate-limit.conf` (limit_req_zone) и `conf.d\alyaeva.pro.conf`.
3. Первый сертификат (после того как DNS резолвится):
   ```
   docker run --rm -v E:/DockerData/nginx/certbot-www:/var/www/certbot ^
     -v E:/DockerData/nginx/certs:/etc/letsencrypt certbot/certbot certonly ^
     --webroot -w /var/www/certbot -d alyaeva.pro -d www.alyaeva.pro ^
     --email <ваша@почта> --agree-tos --no-eff-email
   ```
   Скопировать `live/alyaeva.pro/fullchain.pem` и `privkey.pem` в `certs/alyaeva.pro/`.
   До выпуска сертификата положите заглушку self-signed, иначе nginx не стартует:
   `openssl req -x509 -nodes -newkey rsa:2048 -days 1 -keyout privkey.pem -out fullchain.pem -subj "/CN=alyaeva.pro"`
4. `docker compose -f deploy/docker-compose.yml up -d` (или добавить сервис `site` в существующий compose прокси).
5. Проверка: `docker exec proxy nginx -t && docker exec proxy nginx -s reload`; затем https://alyaeva.pro.
6. Продление сертификата: раз в 60 дней тем же certbot renew + `nginx -s reload` (повесить на планировщик).

## Чек-лист безопасности
- [ ] 80 → 301 → 443 включён.
- [ ] HSTS, CSP, X-Frame-Options, nosniff, Referrer-Policy — в конфе выше.
- [ ] Контейнер `site` не публикует портов наружу.
- [ ] Логи nginx не содержат QUERY-строки форм (форм нет — ок).
- [ ] Резервная копия `index.html` и сертификатов.
