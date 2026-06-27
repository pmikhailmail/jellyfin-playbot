# Предварительный план: автоматический запуск Jellyfin на TCL 65T8C

Дата: 2026-06-27
Статус: Черновик (дизайн, без реализации кода)

## 1) Цель
Собрать сценарий, который:
- включает телевизор (как кнопкой Power на пульте),
- открывает приложение Jellyfin,
- запускает конкретный media item.

## 2) Устройство
- Бренд/модель: TCL 65T8C
- Класс устройства: Smart TV на Google TV
- По карточке товара отмечено: Google TV, Google Assistant built-in, HDMI/CEC/LAN (как признаки платформы и интеграций)

Примечание: часть возможностей Wake из глубокого сна может зависеть от конкретных настроек энергосбережения ТВ.

## 3) Архитектура (дизайн)
1. Orchestrator (Python) — управляет шагами, таймаутами, ретраями и fallback.
2. TV Driver (Google TV) — wake/remote-команды/запуск приложения.
3. Jellyfin Driver — работа с API Jellyfin для target item.
4. Fallback каналы:
   - HDMI-CEC (через подключенный источник),
   - IR blaster (последний резерв для Power).

## 4) Сценарий выполнения
1. Получить item_id (или правило выбора) из Jellyfin.
2. Проверить доступность ТВ в сети.
3. Если ТВ спит/оффлайн -> выполнить wake.
4. Дождаться READY (таймаут + повтор).
5. Запустить Jellyfin app.
6. Передать item через deep link/intent.
7. Проверить факт начала playback.
8. Если не стартовало -> fallback: remote navigation (DPAD/OK) до контента.

## 5) State machine
- OFF
- WAKING
- HOME_READY
- APP_OPENING
- ITEM_STARTING
- PLAYING
- FAILED (с причиной)

## 6) Fallback стратегия
1. Network wake (первичный).
2. HDMI-CEC power on (если есть совместимый источник).
3. IR power toggle (резерв).
4. Если deep link неустойчив -> навигация как пультом.

## 7) Конфиг (что должно быть)
- tv.type = tcl_google_tv
- tv.ip, tv.mac
- network_standby / energy settings
- jellyfin.server_url, jellyfin.token, jellyfin.user_id
- playback.item_id (или правило выбора)
- таймауты: wake/app_open/playback_start
- retries и fallback_order

## 8) Риски
- Wake из глубокого standby может быть нестабилен без CEC/IR.
- Deep link поведение зависит от версии Jellyfin app на TV.
- После апдейтов UI TV навигационные макросы могут требовать корректировки.

## 9) Что уточнить перед реализацией
- Точное поведение ТВ в standby (Network standby / Quick start).
- Есть ли внешний HDMI-источник с CEC.
- Jellyfin установлен нативно на ТВ или через приставку.
- Формат запуска: кнопка, расписание, голос.

## 10) Рекомендованный следующий шаг
Собрать технический blueprint v2: точные команды, таймауты, retries и критерии успеха на каждом шаге.
