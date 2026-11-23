# Манифест 12 — SEO, мета и соцпревью (добавлено 23.11)

## Обязательные теги
- title: «Алёна Аляева — именные книжки-сказки, фотокниги и нейрофото | Екатеринбург» (≤ 70 симв.)
- description ≤ 160 симв., с ключом «именные книжки-сказки».
- canonical: https://alyaeva.pro/
- lang="ru", viewport, theme-color #4B4690.

## Open Graph / Twitter (критично: трафик из Telegram/VK)
- og:title/description/url/image (1200×1200, ≤ 300 KB — WebP/Png).
- twitter:card = summary_large_image.

## JSON-LD
- ProfessionalService: name, url, telephone, image, address (Екатеринбург), areaServed (Россия), sameAs (Telegram/VK/WhatsApp), makesOffer (услуги + цена от 2000 ₽).

## Файлы
- robots.txt — Allow + Sitemap.
- sitemap.xml — одна страница, priority 1.0.
- 404.html — брендированная, с кнопкой на главную, x-robots: noindex через page.

## Производительность
- Шрифты локальные woff2 (без Google CDN — быстрее и без внешних cookies).
- Картинки: WebP + PNG-фолбэк через <picture>, lazy + decoding=async.
- Видео: preload=metadata, poster, не автоплей (экономия трафика).
