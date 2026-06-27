# Запуск воспроизведения через 2 скрипта

Дата: 2026-06-27

Этот документ показывает, как запускать два скрипта подряд, чтобы получить реальное воспроизведение на TV через Jellyfin.

## Какие 2 скрипта используются

1. `jellyfin_nl_to_item_id_e2e.py`
- Преобразует текстовый запрос в конкретный `item_id` Jellyfin.

2. `app-specific/jellyfin/jellyfin_resume_if_ready.py`
- Проверяет доступность TV по ADB, выводит Jellyfin на передний план и запускает найденный `item_id` через Sessions API (если передан токен).

## Предпосылки

- Запускать команды из корня проекта: `C:\Users\mihai\projects\tv_experiments`
- TV доступен по ADB: `192.168.0.122:5555`
- Выставлены переменные окружения Jellyfin:
  - `JELLYFIN_SERVER_URL`
  - `JELLYFIN_USER_NAME`
  - `JELLYFIN_API_KEY`

## Вариант A (рекомендуется): токен через переменную окружения

В PowerShell:

```powershell
$env:JELLYFIN_SERVER_URL = "http://192.168.0.104:8899"
$env:JELLYFIN_USER_NAME = "smarttv"
$env:JELLYFIN_API_KEY = "<YOUR_JELLYFIN_API_KEY>"
```

### Шаг 1: получить item_id из текстового запроса

```powershell
conda run -p .conda python jellyfin_nl_to_item_id_e2e.py `
  --request "включи серию gravity falls про русалдо в бассейне" `
  --use-episode-metadata `
  --pretty `
  --output .\last_resolve.json
```

### Шаг 2: запустить воспроизведение найденного item_id

```powershell
$itemId = (Get-Content .\last_resolve.json -Raw | ConvertFrom-Json).resolved_item.item_id
conda run -p .conda python app-specific/jellyfin/jellyfin_resume_if_ready.py `
  --ip 192.168.0.122 `
  --item-id "$itemId"
```

## Вариант B: передавать токен явно в каждой команде

```powershell
conda run -p .conda python jellyfin_nl_to_item_id_e2e.py `
  --request "включи серию gravity falls про русалдо в бассейне" `
  --server-url "http://192.168.0.104:8899" `
  --jellyfin-token "<YOUR_JELLYFIN_TOKEN>" `
  --use-episode-metadata `
  --pretty `
  --output .\last_resolve.json

$itemId = (Get-Content .\last_resolve.json -Raw | ConvertFrom-Json).resolved_item.item_id
conda run -p .conda python app-specific/jellyfin/jellyfin_resume_if_ready.py `
  --ip 192.168.0.122 `
  --server-url "http://192.168.0.104:8899" `
  --jellyfin-token "<YOUR_JELLYFIN_TOKEN>" `
  --item-id "$itemId"
```

## Как понять, что все прошло успешно

Во втором скрипте ожидаемые строки:

- `Attempting Sessions API playback for item: ...`
- `Sessions API PlayNow status: 204`
- `Sessions API playback confirmed: <название>`

Если видишь эти строки, конкретный медиафайл действительно запущен на TV.

## Быстрый fallback (без шага 1)

Если `item_id` уже известен, можно сразу запускать только второй скрипт:

```powershell
conda run -p .conda python app-specific/jellyfin/jellyfin_resume_if_ready.py `
  --ip 192.168.0.122 `
  --jellyfin-token "$env:JELLYFIN_API_KEY" `
  --item-id "eaf2c5fa89d66ea8c663dd6e58ab4bf9"
```
