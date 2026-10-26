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
- реальная печать выключена по умолчанию;
- полностью автономный mock/dry-run flow;
- сборка одного `.exe` через PyInstaller.

Серверные endpoints в Boxy пока не реализованы. `HttpBoxyClient` фиксирует ожидаемый контракт, а `MockBoxyClient` позволяет проверить агент без production Boxy и без физического принтера.

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

Без `--real-print` агент всегда выбирает `DryRunPrinterBackend`; Windows spooler не вызывается. Настоящая печать требует явного opt-in и конкретного локального принтера:

```powershell
.\.venv\Scripts\python.exe -m boxy_print_agent `
  --server-url https://boxy.example.com `
  --printer "Zebra ZD421" `
  --real-print --once
```

Не запускайте эту команду до реализации и проверки серверной части Boxy. Агент не переключается на default printer, если сохранённый принтер исчез.

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
    "dry_run": false,
    "operating_system": "Windows",
    "agent_version": "0.1.0"
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
  "agent_version": "0.1.0",
  "status": "ONLINE",
  "capabilities": {
    "artifact_content_types": ["image/png"],
    "at_most_once_ledger": true,
    "dry_run": false
  }
}
```

Heartbeat failure не блокирует текущий poll; основной цикл применяет bounded exponential backoff к сетевым ошибкам.

### Atomic claim-next

`POST /devices/{device_id}/jobs/claim-next`

Сервер в одной транзакции выбирает только `PENDING` job этого устройства и переводит его в `CLAIMED`. Один job нельзя вернуть двум конкурентным агентам. Нет задания — `204 No Content`.

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

Перед первым вызовом Windows spooler агент синхронно пишет `PRINT_INTENT` в SQLite с `journal_mode=WAL` и `synchronous=FULL`.

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

После появления строки тот же `(job_id, print_attempt_token)` никогда не отправляется в spooler автоматически повторно. Если процесс упал после `PRINT_INTENT`, невозможно надёжно доказать, был ли принят job Windows spooler: после рестарта attempt становится `REVIEW_REQUIRED`. Пользователь решает, нужен ли reprint; сервер обязан создать новый `print_attempt_token`.

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

Тесты не требуют Boxy, production credentials или физического принтера. Они покрывают pairing, секреты/редакцию, atomic mock claim, HTTP timeout, artifact validation, printer errors, backoff, duplicate attempts, explicit reprint и crash/restart после `PRINT_INTENT`.

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
- transactional `claim-next` с row locking и защитой от конкурентного claim;
- artifact endpoint только для устройства-владельца и claimed job;
- строгая state machine и идемпотентные status callbacks;
- явный reprint, который создаёт новый `print_attempt_token`;
- online/offline по `last_seen`, audit и UI `REVIEW_REQUIRED`;
- retention/cleanup jobs и artifacts без нарушения audit/ledger semantics.

Эти изменения намеренно не входят в данный репозиторий.
