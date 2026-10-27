# Boxy Print Agent

Отдельный Windows-агент, который получает назначенные ему задания печати из Boxy по HTTP API и отправляет заранее подготовленные PNG-стикеры на один явно выбранный локальный принтер.

Это самостоятельный проект. Он не импортирует Django-код, не читает базу Boxy, не имеет доступа к WB API и не требует размещения рядом с `WB_APP`.

## Состояние MVP

- Python 3.10+ и Windows;
- HTTP polling без WebSocket;
- одно логическое устройство и один выбранный Windows-принтер;
- PNG artifacts; PDF намеренно отклоняется;
- pairing по одноразовому коду;
- scoped device token в Windows DPAPI, не в `config.json`;
- локальный SQLite ledger для защиты от повторной печати;
- пакетная печать поставки одним Windows spooler document без polling-пауз между этикетками;
- реальная печать выключена по умолчанию;
- фоновый режим и per-user автозапуск Windows через HKCU Run;
- полностью автономный mock/dry-run flow;
- сборка одного `.exe` через PyInstaller.

Серверные endpoints реализованы в `WB_APP`. Этот README остаётся нормативным контрактом клиента; `MockBoxyClient` позволяет проверить агент без production Boxy и без физического принтера.

## Архитектура

```text
браузер / телефон / ПК
          |
          v
      Boxy server
          | HTTPS + scoped device token
          v
  Boxy Print Agent
    | polling / claim / status
    | SQLite at-most-once ledger
    v
 выбранный Windows printer
```

Основные модули:

- `api_client.py` — фиксированный least-privilege HTTP-клиент;
- `polling.py` — heartbeat, atomic claim и orchestration;
- `artifact.py` — MIME, размер, SHA-256, PNG и dimensions validation;
- `idempotency.py` — durable SQLite ledger;
- `printer.py` — `DryRunPrinterBackend` и Windows GDI backend;
- `secrets.py` — хранение device token через Windows DPAPI;
- `app.py` — application service, общий для CLI и GUI;
- `gui.py` — тонкая оболочка tkinter без логики печати;
- `mock_api.py` — потокобезопасный dev-контракт.

## Что умеет агент

- подключается к Boxy одноразовым pairing code;
- показывает только локальные Windows-принтеры и печатает на явно выбранный принтер;
- получает одиночные задания: QR поставки, явный reprint и другие PNG-артефакты;
- получает все стикеры одной поставки атомарной пачкой;
- проверяет MIME, размер, SHA-256, PNG-структуру и физический размер каждого стикера до печати;
- печатает пачку одним `StartDoc`, используя отдельную `StartPage/EndPage` для каждой этикетки;
- сохраняет отдельный audit и at-most-once attempt для каждого стикера пачки;
- после неоднозначного сбоя не печатает пачку автоматически повторно, а требует проверки оператора;
- работает в окне, в фоне или через автозапуск Windows;
- поддерживает безопасный dry-run без обращения к физическому принтеру.

Одна кнопка «Печать» в поставке создаёт в Boxy отдельные immutable `PrintJob` для аудита, но общий batch claim объединяет их в один документ Windows spooler. Поэтому этикетки выходят подряд без двух-трёхсекундной polling-паузы. Одиночные задания остаются одиночными документами.

## Установка на складской Windows-компьютер

Требования:

- Windows 10/11 под тем Windows-пользователем, который будет постоянно печатать;
- установленный драйвер принтера и успешная тестовая этикетка из Windows;
- постоянный доступ компьютера к HTTPS-адресу Boxy;
- готовый `BoxyPrintAgent.exe` из `dist` или собранный по инструкции ниже.

1. Создайте постоянную папку, например `C:\BoxyPrintAgent`, и скопируйте туда `BoxyPrintAgent.exe`. После включения автозапуска EXE нельзя переносить без повторной установки автозапуска.
2. Узнайте точное имя принтера в Windows, например `Xprinter XP-365B`.
3. В Boxy откройте «Настройки → Печать», создайте одноразовый код подключения. Код короткоживущий и используется один раз.
4. В PowerShell из папки агента выполните первоначальный запуск:

```powershell
.\BoxyPrintAgent.exe `
  --server-url https://boxy.example.com `
  --printer "Xprinter XP-365B" `
  --real-print `
  --gui
```

5. В открывшемся окне введите pairing code и нажмите «Подключить устройство».
6. Нажмите «Проверить подключение». В Boxy устройство должно стать online и получить выбранный Windows-принтер.
7. Сделайте тестовую дистанционную печать из Boxy. Для поставки зелёная кнопка ставит всю пачку в один spooler document.
8. Закройте окно и включите автозапуск:

```powershell
.\BoxyPrintAgent.exe --install-autostart
```

9. Запустите фон сразу, не дожидаясь следующего входа в Windows:

```powershell
.\BoxyPrintAgent.exe --background
```

Administrator не требуется: автозапуск устанавливается только для текущего пользователя через `HKCU`. Этот пользователь должен быть авторизован в Windows, иначе его принтеры и DPAPI-секрет недоступны.

Для локального тестового Boxy допустим HTTP только на `localhost` и только с явным dev mode. Production должен использовать HTTPS с обычной проверкой сертификата.

## Активация, ежедневная работа и обновление

После pairing повторно вводить код не нужно. `device_id` хранится в обычном конфиге, а scoped device token — отдельно в DPAPI и расшифровывается только тем же Windows-пользователем.

Ежедневная работа:

1. Windows-пользователь входит в систему.
2. Агент запускается с `--background` через автозапуск.
3. Heartbeat переводит устройство в online.
4. Boxy атомарно выдаёт одиночное задание или всю пачку.
5. Агент проверяет все файлы, фиксирует attempts и отправляет один документ в Windows spooler.

Обновление EXE:

1. Завершите старый процесс агента.
2. Замените `BoxyPrintAgent.exe` в той же постоянной папке.
3. Запустите `BoxyPrintAgent.exe --background` или перезайдите в Windows.
4. Pairing, выбранный принтер и ledger сохраняются в `%LOCALAPPDATA%\BoxyPrintAgent` и не требуют удаления.

Если путь к EXE изменился, повторите `--install-autostart`. Для отключения фонового запуска используйте:

```powershell
.\BoxyPrintAgent.exe --remove-autostart
```

## Быстрый старт для разработки

Все команды выполняются из корня этого репозитория. Не используйте окружение другого проекта.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

Полный mock-flow без Windows spooler:

```powershell
.\.venv\Scripts\python.exe -m boxy_print_agent --dev --dry-run --pair --once
```

Введите любой ещё не использованный в этом процессе dev-код. Mock возвращает только фиктивный `dev-scoped-token-not-for-production`. Для изолированного конфига можно передать `--config C:\temp\boxy-agent-dev\config.json`.

GUI в безопасном режиме:

```powershell
.\.venv\Scripts\python.exe -m boxy_print_agent --dev --dry-run --gui
```

Новый config безопасно создаётся с `dry_run=true`. Флаги `--real-print` и `--dry-run` меняют и сохраняют этот режим; последующие обычные и фоновые запуски используют сохранённое значение. Настоящая печать требует однократного явного opt-in и конкретного локального принтера:

```powershell
.\.venv\Scripts\python.exe -m boxy_print_agent `
  --server-url https://boxy.example.com `
  --printer "Zebra ZD421" `
  --real-print --gui
```

Агент не переключается на системный default printer, если сохранённый принтер исчез: задание получает конфигурационную ошибку до spooler dispatch.

## Режимы запуска

Собранный `BoxyPrintAgent.exe` является windowed-приложением и не открывает консоль. Основные режимы:

- `--gui` — окно состояния, pairing, выбор принтера и открытие логов;
- `--background` — постоянный фоновый polling;
- `--once` — один heartbeat/claim cycle для диагностики;
- `--real-print` — явно сохранить разрешение настоящей печати;
- `--dry-run` — явно сохранить безопасный режим без spooler;
- `--install-autostart` / `--remove-autostart` — управление автозапуском текущего пользователя.

После pairing, выбора точного Windows-принтера и сохранения real-print mode включите автозапуск один раз:

```text
BoxyPrintAgent.exe --install-autostart
```

Команда идемпотентно добавляет текущий абсолютный путь EXE в `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` с аргументом `--background`. Administrator и PowerShell не требуются. При входе текущего Windows-пользователя агент запускается без окна, продолжает polling после временной потери сети с bounded backoff и использует сохранённый printer name.

Проверка вручную без установки автозапуска:

```text
BoxyPrintAgent.exe --background
```

Отключение автозапуска:

```text
BoxyPrintAgent.exe --remove-autostart
```

Tray-иконка в этой версии не используется; состояние устройства и очередь видны в Boxy в разделе «Настройки → Печать», подробности — в `%LOCALAPPDATA%\BoxyPrintAgent\logs\agent.log`.

## Локальные данные

По умолчанию данные размещаются в `%LOCALAPPDATA%\BoxyPrintAgent`:

- `config.json` — только несекретные настройки;
- `device-token.dpapi` — DPAPI ciphertext текущего Windows-пользователя;
- `print-ledger.sqlite3` — технический журнал attempts;
- `logs\agent.log` — rotating log без токенов и artifact body.

Пример несекретного конфига находится в `config.example.json`. Ключи `device_token`, `token`, `authorization`, `pairing_code` и `wb_api_token` в JSON отклоняются. Production URL обязан использовать HTTPS; plain HTTP допустим только для `localhost` в явном dev mode. TLS certificate verification не отключается, redirects не принимаются.

## Pairing

1. Пользователь создаёт в Boxy короткоживущий одноразовый pairing code.
2. Агент отправляет его только в `POST /api/print-agent/v1/pair` вместе с display name, hostname и capabilities.
3. Boxy атомарно погашает код, создаёт или привязывает `PrintDevice`, возвращает `device_id` и scoped token.
4. Агент сохраняет `device_id` в обычный config, а token — отдельно через DPAPI.

Token должен разрешать только heartbeat, atomic claim заданий этого устройства, скачивание artifact уже claimed job и status callback этого job. Он не должен давать доступ к складу, бухгалтерии, пользователям или WB API.

Пример pairing request:

```json
{
  "pairing_code": "12345678",
  "display_name": "Складской ПК",
  "hostname": "WAREHOUSE-01",
  "capabilities": {
    "artifact_content_types": ["image/png"],
    "at_most_once_ledger": true,
    "batch_spooler_document": true,
    "dry_run": false,
    "operating_system": "Windows",
    "agent_version": "0.2.0"
  }
}
```

Response:

```json
{
  "device_id": "6d23916c-1bdf-49e2-a472-dcff6b69a321",
  "device_token": "opaque-scoped-secret",
  "display_name": "Складской ПК"
}
```

## HTTP API contract

Все production endpoints находятся под `/api/print-agent/v1`, используют HTTPS и, кроме pairing, требуют `Authorization: Bearer <device-token>`.

### Heartbeat

`POST /devices/{device_id}/heartbeat`

```json
{
  "device_id": "6d23916c-1bdf-49e2-a472-dcff6b69a321",
  "display_name": "Складской ПК",
  "hostname": "WAREHOUSE-01",
  "selected_printer": "Zebra ZD421",
  "agent_version": "0.2.0",
  "status": "ONLINE",
  "capabilities": {
    "artifact_content_types": ["image/png"],
    "at_most_once_ledger": true,
    "batch_spooler_document": true,
    "dry_run": false
  }
}
```

Heartbeat failure не блокирует текущий poll; основной цикл применяет bounded exponential backoff к сетевым ошибкам.

### Atomic claim-next-batch

`POST /devices/{device_id}/jobs/claim-next-batch`

Основной endpoint агента 0.2+. Сервер в одной PostgreSQL-транзакции выбирает первый `PENDING` job устройства. Если он относится к UI-пачке, сервер блокирует и переводит в `CLAIMED` все оставшиеся задания той же пачки; иначе возвращает singleton batch. Порядок `jobs` является порядком страниц принтера. Нет задания — `204 No Content`.

```json
{
  "batch_id": "b.64-lowercase-hex-characters",
  "jobs": [
    {
      "job_id": "job-123",
      "device_id": "6d23916c-1bdf-49e2-a472-dcff6b69a321",
      "print_attempt_token": "attempt-opaque-1",
      "status": "CLAIMED",
      "copies": 1,
      "artifact": {
        "artifact_id": "artifact-123",
        "content_type": "image/png",
        "byte_size": 48213,
        "sha256": "64-lowercase-hex-characters",
        "width_px": 480,
        "height_px": 320,
        "width_mm": 58.0,
        "height_mm": 40.0,
        "dpi": null,
        "orientation": "landscape"
      }
    }
  ]
}
```

Агент сначала скачивает и проверяет все artifacts. Затем одной SQLite-транзакцией записывает `PRINT_INTENT` для каждого job и вызывает Windows GDI один раз: один `StartDoc`, несколько `StartPage/EndPage`, один `EndDoc`. Все jobs получают одинаковый `spooler_job_id`, но сохраняют отдельные статусы и audit.

Если endpoint отсутствует (`404`), агент 0.2 безопасно использует legacy `claim-next` и печатает singleton. Это обеспечивает безопасный порядок обновления: сначала WB_APP, затем EXE.

### Legacy atomic claim-next

`POST /devices/{device_id}/jobs/claim-next`

Endpoint сохранён для агентов 0.1 и диагностики. Сервер в одной транзакции выбирает только один `PENDING` job этого устройства и переводит его в `CLAIMED`. Один job нельзя вернуть двум конкурентным агентам. Нет задания — `204 No Content`.

```json
{
  "job_id": "job-123",
  "device_id": "6d23916c-1bdf-49e2-a472-dcff6b69a321",
  "print_attempt_token": "attempt-opaque-1",
  "status": "CLAIMED",
  "copies": 1,
  "artifact": {
    "artifact_id": "artifact-123",
    "content_type": "image/png",
    "byte_size": 48213,
    "sha256": "64-lowercase-hex-characters",
    "width_px": 480,
    "height_px": 320,
    "width_mm": 60.0,
    "height_mm": 40.0,
    "dpi": 203,
    "orientation": "portrait"
  }
}
```

### Artifact

`GET /jobs/{job_id}/artifact`

Никаких arbitrary URLs. Сервер проверяет ownership, claim и token. Клиент потоково ограничивает размер, проверяет `Content-Type`, metadata byte size, SHA-256, структуру PNG, pixels и physical dimensions до обращения к spooler.

### Status callback

`POST /jobs/{job_id}/status`

```json
{
  "status": "DISPATCHED_TO_SPOOLER",
  "print_attempt_token": "attempt-opaque-1",
  "code": "",
  "message": "",
  "spooler_job_id": "42"
}
```

Каждый callback получает `Idempotency-Key: {job_id}:{print_attempt_token}:{status}`. Допустимые состояния:

```text
PENDING -> CLAIMED -> PRINT_INTENT -> DISPATCHED_TO_SPOOLER -> ACKNOWLEDGED
                  \-> ERROR (только до PRINT_INTENT)
                   -> REVIEW_REQUIRED (любая неоднозначность после PRINT_INTENT)
```

`ACKNOWLEDGED` означает, что агент получил успешный ответ Boxy на отправку spooler status и завершил локальную запись. Ни `DISPATCHED_TO_SPOOLER`, ни `ACKNOWLEDGED` не доказывают физический выход этикетки. UI должен писать «Отправлено на принтер», а не «Напечатано».

## At-most-once и SQLite ledger

Перед первым вызовом Windows spooler агент синхронно пишет `PRINT_INTENT` в SQLite с `journal_mode=WAL` и `synchronous=FULL`. Для пачки все строки создаются одной SQLite-транзакцией до `StartDoc`.

```sql
CREATE TABLE print_attempts (
    job_id TEXT NOT NULL,
    print_attempt_token TEXT NOT NULL,
    state TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL,
    printer_name TEXT NOT NULL,
    spooler_job_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_error TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (job_id, print_attempt_token)
);
```

После появления строки тот же `(job_id, print_attempt_token)` никогда не отправляется в spooler автоматически повторно. Если процесс упал после batch `PRINT_INTENT`, невозможно надёжно доказать, какие страницы физически вышли: вся пачка требует проверки, а автоматическая повторная печать запрещена. Пользователь решает, какие labels требуют явного reprint; сервер создаёт новые `print_attempt_token`.

Это консервативная защита от двойной этикетки, а не exactly-once proof. Внешние границы — HTTP, SQLite и Windows spooler — не образуют общую транзакцию.

Ledger не хранит device token, Authorization, WB token или содержимое стикера.

## Threat model

Агент считает недоверенными сеть, API responses, artifact bytes и job metadata. Основные меры:

- production HTTPS с обычной TLS verification;
- scoped token и отдельный pairing flow;
- DPAPI encryption at rest;
- фиксированные Boxy endpoints без server-provided URL/path/command;
- только локально перечисленный и явно выбранный printer;
- запрет silent fallback на Windows default printer;
- allowlist `image/png`, max bytes, SHA-256 и Pillow verification;
- ровно одна копия на attempt в MVP;
- sanitized errors, redacted logs, отсутствие artifact body в логах;
- durable unique ledger key `(job_id, print_attempt_token)`;
- real print только с `--real-print`.

Компрометация Windows-аккаунта пользователя или самого принтера вне границ защиты агента. Server-side обязан проверять device ownership и разрешённые state transitions независимо от клиента.

## Тесты

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Тесты не требуют Boxy, production credentials или физического принтера. Они покрывают pairing, секреты/редакцию, atomic single/batch claim, один spooler document для нескольких PNG, HTTP timeout, artifact validation, printer errors, backoff, duplicate attempts, explicit reprint и crash/restart после `PRINT_INTENT`.

## Диагностика

- Устройство offline: проверьте, что процесс запущен, URL Boxy доступен и pairing не отключён в «Настройки → Печать».
- Принтер недоступен: откройте GUI, выберите точное имя установленного Windows-принтера и нажмите «Сменить принтер».
- Задание остаётся в очереди: проверьте `%LOCALAPPDATA%\BoxyPrintAgent\logs\agent.log` и online-состояние устройства.
- `REVIEW_REQUIRED`: spooler мог принять часть или всю пачку. Сначала физически проверьте этикетки, затем используйте явный reprint только для недостающих.
- После обновления пачка снова печатается с паузами: убедитесь, что работает агент 0.2+, а WB_APP содержит endpoint `claim-next-batch`; fallback на старый сервер намеренно печатает по одному.
- Агент не должен запускаться одновременно двумя процессами под одним device token. Серверный atomic claim защищает job, но два процесса усложняют локальную диагностику.

## Сборка EXE

```powershell
.\build.ps1
```

Скрипт сначала запускает весь pytest suite и только затем PyInstaller. Результат: `dist\BoxyPrintAgent.exe`. Каталоги `build/`, `dist/`, runtime config, logs, SQLite и secrets исключены из Git; hand-maintained `BoxyPrintAgent.spec` коммитится.

Безопасная проверка собранного файла:

```powershell
.\dist\BoxyPrintAgent.exe --help
```

## Что требуется реализовать на стороне Django Boxy

- модели `PrintDevice`, `PrintJob`, immutable artifact и attempt/status history;
- краткоживущие одноразовые pairing codes с атомарным consume;
- безопасное хранение hash device token и возможность revoke/rotate;
- device-scoped authentication и object-level authorization;
- transactional `claim-next` и `claim-next-batch` с row locking и защитой от конкурентного claim;
- artifact endpoint только для устройства-владельца и claimed job;
- строгая state machine и идемпотентные status callbacks;
- явный reprint, который создаёт новый `print_attempt_token`;
- online/offline по `last_seen`, audit и UI `REVIEW_REQUIRED`;
- retention/cleanup jobs и artifacts без нарушения audit/ledger semantics.

Эти изменения намеренно не входят в данный репозиторий.
