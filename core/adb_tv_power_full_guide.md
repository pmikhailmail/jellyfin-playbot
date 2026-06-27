# TCL Google TV + ADB: полный путь до рабочего включения/выключения

Дата: 2026-06-27

## 1. Цель
Собрать рабочий тестовый цикл:
- включить ТВ по сети (Wake-on-LAN),
- выключить ТВ командой через ADB,
- запускать это из одного Python-скрипта.

Проектный скрипт:
- `tv_power_test.py`

Текущие параметры ТВ:
- MAC: `2c:1b:3a:c3:d8:2d`
- IP (DHCP reservation/static lease): `192.168.0.122`

---

## 2. Что нужно установить на ПК

### 2.1 Python
Нужен Python 3.10+ (у нас использовался conda env).

### 2.2 Android Platform-Tools (ADB)
На Windows установили через winget:

```powershell
winget install --id Google.PlatformTools --exact --source winget --accept-source-agreements --accept-package-agreements
```

Проверка:

```powershell
adb version
```

Если в текущей сессии PowerShell команда `adb` не находится сразу после установки, открой новую сессию или добавь путь вручную:

```powershell
$env:Path = "C:\Users\mihai\AppData\Local\Microsoft\WinGet\Packages\Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe\platform-tools;" + $env:Path
```

---

## 3. Что включить на телевизоре (обязательно)

### 3.1 Режим энергопотребления для сетевого пробуждения
В меню питания/энергосбережения выбрать режим с максимальной сетевой активностью в standby.
У нас это был третий режим (с упоминанием Google Assistant и сетевых функций).

### 3.2 Developer options
1. Открыть Settings -> System -> About.
2. Нажать 7 раз по Build/OS version.
3. Появятся Developer options.

### 3.3 Debugging
В Developer options:
1. Включить `USB debugging`.
2. Если есть `Wireless debugging`/`Network debugging` — включить тоже.

Важно: даже для работы по сети часто нужен именно главный тумблер `USB debugging`.

### 3.4 Подтверждение RSA-ключа
Это НЕ команда в терминале, а всплывающее окно на телевизоре.

Сначала на ПК выполняется команда с IP и портом, например:

```powershell
adb connect 192.168.0.122:5555
```

После этого на ТВ появится окно:
- Allow USB debugging?

Нужно выбрать:
- `Always allow from this computer`
- `Allow`

Без этого устройство будет `unauthorized`.

---

## 4. Фактический путь, который сработал

### Шаг 1: Включение ТВ
Команда:

```powershell
python tv_power_test.py on --ip 192.168.0.122 --mac 2c:1b:3a:c3:d8:2d --wait 6
```

Результат: успешно. ТВ включается по Wake-on-LAN.

### Шаг 2: Первая попытка выключения
Команда `off` не работала, пока ADB не был доступен:
- сначала не было adb на ПК,
- затем `5555` был закрыт,
- потом устройство было `unauthorized`.

### Шаг 3: После включения USB debugging
Порт `5555` стал открыт, `adb connect` начал доходить до авторизации.

Проверка:

```powershell
adb connect 192.168.0.122:5555
adb devices
```

Ожидаемый статус после подтверждения на ТВ:
- `192.168.0.122:5555 device`

### Шаг 4: Рабочее выключение
Команда:

```powershell
python tv_power_test.py off --ip 192.168.0.122 --mac 2c:1b:3a:c3:d8:2d
```

Результат: успешно, `Power-off command sent.`

---

## 5. Как запускать полный цикл сейчас

### Вариант вручную (по одной команде)
1. Включить:

```powershell
python tv_power_test.py on --ip 192.168.0.122 --mac 2c:1b:3a:c3:d8:2d --wait 6
```

2. Выключить:

```powershell
python tv_power_test.py off --ip 192.168.0.122 --mac 2c:1b:3a:c3:d8:2d
```

---

## 6. Диагностика и типовые ошибки

### Ошибка: `adb is not found in PATH`
Причина: platform-tools не установлен или путь не подхватился.
Решение:
1. Установить через winget.
2. Перезапустить терминал или временно добавить путь platform-tools в `$env:Path`.

### Ошибка: `cannot connect ... 5555` / `actively refused`
Причина: ТВ не слушает ADB по сети.
Решение:
1. Включить USB debugging.
2. Проверить Wireless/Network debugging.
3. Повторить `adb connect`.

### Статус: `unauthorized`
Причина: не подтвержден RSA-ключ на ТВ.
Решение:
1. Подтвердить диалог на ТВ.
2. Выбрать `Always allow from this computer`.
3. Повторить `adb connect` и `adb devices`.

### Статус: `offline` на других портах (например 6466/6467)
Это не рабочий канал для нашего управления питанием в данном сценарии.
Рабочий канал был через `192.168.0.122:5555` после включения debugging.

---

## 7. Минимальный чек-лист перед каждым запуском

1. ТВ и ПК в одной LAN.
2. У ТВ корректный IP (у нас `192.168.0.122`).
3. На ТВ включен режим энергопотребления, допускающий сетевую активность в standby.
4. Включен USB debugging.
5. После `adb connect` устройство в `adb devices` видно как `device`.

---

## 8. Что важно для будущего Docker/NAS

1. Этот подход переносится на Linux/Docker.
2. В контейнере должны быть:
- Python,
- adb (platform-tools).
3. Для WoL обычно лучше `network_mode: host`.
4. ADB авторизация (RSA) должна быть уже выполнена хотя бы один раз.

---

## 9. Итог
Сценарий power cycle рабочий:
- ON: Wake-on-LAN работает,
- OFF: ADB работает после включения USB debugging и авторизации ключа на ТВ.
