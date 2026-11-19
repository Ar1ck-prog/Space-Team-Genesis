# Контракт API FinTech-среды

**Версия:** 1.1.0 · **Статус:** 1.0.0 согласована командой; изменения 1.1.0 (раздел 10) предложены в PR по issue #2 и ждут ревью · **Дата:** 2026-11-19.

Этот контракт описывает будущую воспроизводимую RL-среду для расследования синтетических финансовых ошибок. Он не означает, что API уже реализован. При неоднозначности кейса пометка «решение команды» означает минимальное согласованное решение.

## 1. Границы компонентов

| Компонент | Ответственность | Не делает |
| --- | --- | --- |
| Генератор синтетических компаний | По `(seed, config, environment_version)` создаёт открытые записи/документы и закрытые `truth.json`/`provenance.json`. | Не раскрывает эталон агенту. |
| Среда | Изолирует состояние эпизода и даёт главный интерфейс `reset`/`step`. | Не даёт доступ к ОС или данным помимо действий. |
| Агент | Видит публичное наблюдение и выбирает действие. | Не видит seed, режим проверки, пути, truth/provenance. |
| Изолированный оценщик | Проверяет submit, matcher и награду по эталону. | Не запускается в процессе агента. |
| HTTP-обёртка | Транспортирует тот же `reset`/`step` для фронтенда. | Не создаёт отдельную семантику и не раскрывает закрытые данные. |

Главный интерфейс — JSON-совместимые `reset`/`step` в стиле Gymnasium. HTTP использует ту же логику и тот же валидатор.

## 2. Общие типы и идентификаторы

* Деньги (`*_minor`) — целые минимальные единицы; разрядность валюты задаёт `manifest.currency_decimals` (например, `KZT: 2`). Все целые, включая seed и счётчики, ограничены безопасным диапазоном JavaScript `-9007199254740991..9007199254740991`; генератор обязан оставаться в нём. В v1 большие целые строками не передаются.
* FX-курсы — непустые десятичные строки без экспоненты, например `"512.3400"`.
* Время — ISO 8601/RFC 3339 со смещением, например `"2026-11-01T09:15:00+05:00"`; дата FX — `YYYY-MM-DD` в политике эпизода.
* Отсутствующая ссылка — явный JSON `null`, не пустая строка и не опущенное поле.
* Поля, названные обязательными, всегда присутствуют. Новые публичные поля запрещены до обновления контракта.

ID — ASCII длиной 1–128, без пробелов; регистр не нормализуется. Канонический ID имеет установленный ниже префикс. У `ledger:`, `invoice:`, `payment:` и `doc:` локальная часть не содержит двоеточие; `fx:` — единственное структурированное исключение и имеет ровно форму `fx:<date>:<source>:<target>`. Массивы ID канонизуются удалением точных дублей и лексикографической сортировкой полного ID. Повтор или неверный префикс делает `submit` структурно некорректным.

| Сущность | ID | Применение |
| --- | --- | --- |
| Проводка | `ledger:<row_id>` | запись, affected/evidence |
| Счёт | `invoice:<invoice_id>` | запись, affected/evidence |
| Платёж | `payment:<payment_id>` | запись, affected/evidence |
| Строка FX | `fx:<date>:<source>:<target>` | запись, evidence |
| Документ | `doc:<doc_id>` | документ, evidence |

`affected_ids` — канонический набор публичных записей, образующих расхождение; `doc:*` запрещён. Для `duplicate_invoice` нужны минимум два `invoice:*`; для `payment_allocation` — один `payment:*` и минимум один `invoice:*`; для `fx_date_or_rate` и `reporting_period` — минимум один `ledger:*`. Точный набор задаёт эталон по правилам раздела 10.3. `evidence_ids` — доступные открытые записи и/или документы, содержащие один полный допустимый набор доказательств; внутренние пути не являются ID.

## 3. Среда

### `reset(seed, config)`

`seed` передаёт только доверенный runner/сервер/тест; агенту и HTTP-клиенту он недоступен. Это безопасное целое `0..9007199254740991`. `config` — обязательный объект:

```json
{
  "dataset_profile": "train",
  "company_size": "small",
  "enabled_issue_types": ["duplicate_invoice", "payment_allocation", "fx_date_or_rate", "reporting_period"],
  "max_steps": 100,
  "time_limit_seconds": 60,
  "evaluation_mode": "open"
}
```

`dataset_profile`: `train` или `dev`; `company_size`: `small`, `medium`, `large`; `enabled_issue_types`: непустой уникальный массив enum раздела 5; `max_steps` в v1 ровно `100`; `time_limit_seconds`: целое `1..60`; `evaluation_mode`: `open` либо `closed`. Режим и seed — только доверенному коду: публичный HTTP их не принимает. Неподдержанное сочетание — ошибка конфигурации до эпизода.

```json
{
  "observation": {
    "kind": "episode_start",
    "task": "Investigate synthetic accounting records for the stated reporting period and submit supported findings.",
    "manifest": {
      "schema_version": "1.0.0",
      "episode_id": "ep_01JABCDEF1234567890",
      "base_currency": "KZT",
      "currency_decimals": {"KZT": 2, "USD": 2},
      "period_start": "2026-11-01T00:00:00+05:00",
      "period_end": "2026-11-30T23:59:59+05:00",
      "timezone": "Asia/Almaty",
      "record_counts": {"ledger": 420, "invoices": 58, "payments": 47, "fx": 30},
      "document_count": 105,
      "rounding_rule": "half_up_to_currency_minor"
    },
    "policy_summary": {"fx_rate_date": "document_timestamp_local_date", "period_boundary": "inclusive_start_and_end", "rounding": "half_up_to_currency_minor", "fx_rate_direction": "target_per_source", "ledger_currency": "base_currency", "accounts": {"1000": "Cash", "1200": "Accounts receivable", "2400": "Unapplied receipts", "4000": "Revenue", "6100": "Rent", "6200": "Salaries", "6300": "Utilities", "6400": "Software subscriptions", "6500": "Bank fees", "7900": "FX gain and loss"}, "invoice_posting": "debit_1200_credit_4000_at_issued_at_local_date_rate", "payment_posting": "debit_1000_at_payment_rate_credit_1200_per_allocation_at_invoice_rate_excess_to_2400_difference_to_7900", "allocation_order": "invoice_refs_in_order_up_to_outstanding", "refunds": "negative_amount_debit_2400_credit_1000"},
    "remaining_steps": 100
  },
  "info": {"episode_id": "ep_01JABCDEF1234567890", "environment_version": "1.0.0", "step": 0}
}
```

`record_counts` всегда содержит все четыре таблицы и неотрицательные безопасные целые. `policy_summary` совпадает с открытым `policy.json` эпизода; все его поля обязательны (раздел 10.2). `info` не содержит seed, режим, truth/provenance, пути или диагностику оценщика.

### `step(action)`

`action` — объект с обязательным discriminator `kind`; все поля конкретного варианта обязательны. Пустой объект, лишнее поле или неизвестный `kind` — некорректное действие.

```json
{"kind":"list_records","table":"ledger","cursor":null,"limit":25}
```
```json
{"kind":"get_record","table":"invoices","id":"invoice:INV-1042"}
```
```json
{"kind":"get_document","doc_id":"doc:DOC-INV-1042"}
```
```json
{"kind":"calculator","expression":"(150000.00 / 512.3400) - 292.77"}
```
```json
{"kind":"submit","findings":{"episode_id":"ep_01JABCDEF1234567890","findings":[]}}
```

Таблицы: только `ledger`, `invoices`, `payments`, `fx`. В `list_records` `cursor` — `null` для первой страницы или непрозрачная строка от прошлой страницы той же таблицы/эпизода; `limit` — целое `1..50`. Порядок фиксирован генератором, один cursor всегда возвращает ту же страницу, `next_cursor` — строка или `null`. У `get_record` допустимы пары `ledger`/`ledger:`, `invoices`/`invoice:`, `payments`/`payment:`, `fx`/`fx:`. `get_document` принимает существующий `doc:*`.

`calculator.expression` — 1–256 символов: десятичные литералы, `+ - * /`, скобки, ASCII-пробелы. Нет имён, функций, индексов, строк, экспоненты, файлов, сети и выполнения кода. Результат — десятичная строка без экспоненты; деление на ноль — ошибка.

```json
{
  "observation": {
    "kind": "records",
    "request": {"kind": "list_records", "table": "ledger", "cursor": null, "limit": 2},
    "records": [{"id": "ledger:L-0001", "timestamp": "2026-11-02T09:00:00+05:00", "account": "4010", "debit_minor": 150000, "credit_minor": 0, "currency": "KZT", "entity_id": "entity:V-17", "invoice_id": "invoice:INV-1042", "payment_id": null}],
    "next_cursor": "c_ledger_0002",
    "remaining_steps": 99
  },
  "reward": 0,
  "terminated": false,
  "truncated": false,
  "info": {"episode_id": "ep_01JABCDEF1234567890", "step": 1}
}
```

Другие успешные `observation.kind`: `record` (`record`), `document` (`document`), `calculation` (`expression`, `result`), `submission` (`accepted_findings`, `remaining_steps`). Каждое успешное наблюдение содержит `request` с принятым действием. Документ счёта: `id`, `invoice_id`, `entity_id`, `issued_at`, `due_at`, `currency`, `total_minor`, `line_items`; документ платежа: `id`, `payment_id`, `timestamp`, `currency`, `amount_minor`, `invoice_refs`. Ссылки — канонические ID или `null`.

Ошибка среды — тоже расходующий шаг, в HTTP это `200`:

```json
{"observation":{"kind":"error","error":{"code":"unknown_id","message":"Requested public identifier was not found."},"remaining_steps":98},"reward":0,"terminated":false,"truncated":false,"info":{"episode_id":"ep_01JABCDEF1234567890","step":2}}
```

Разрешённые публичные коды: `invalid_action`, `invalid_argument`, `invalid_cursor`, `unknown_id`, `unsupported_table`, `unsafe_expression`, `division_by_zero`, `invalid_submission`. Тексты фиксированы и не раскрывают эталон, пути и причины оценки.

## 4. Жизненный цикл

1. `reset` создаёт изолированное состояние без курсоров, документов и счётчиков прошлого эпизода.
2. Бюджет — ровно 100 принятых `step`, включая `submit`; ошибочные действия расходуют шаг. `inspection_steps` — число всех принятых `step`, кроме `submit`, то есть `0..99`.
3. Порядок: запретить вызов после завершения → принять один шаг бюджета → валидировать/выполнить действие → применить завершение.
4. Корректный `submit` на принятом 100-м шаге имеет приоритет: `terminated=true`, `truncated=false`, `inspection_steps=99`. Любой иной 100-й шаг (включая ошибку) даёт `truncated=true`, `terminated=false`, `{"kind":"truncated","result":null,"remaining_steps":0}`.
5. Тайм-аут до следующего шага или при не-submit обработке даёт тот же truncated-ответ; findings не оцениваются. Submit до истечения лимита терминален.
6. Корректный submit всегда терминален. Некорректная структура findings тоже терминальна, с внутренней наградой `-1`; детали пишутся только оценщику.
7. После `terminated`/`truncated` `step` запрещён: HTTP `409 episode_finalized`, бюджет не расходуется.

## 5. Findings, matcher и награда

```json
{
  "episode_id": "ep_01JABCDEF1234567890",
  "findings": [{
    "type": "duplicate_invoice",
    "affected_ids": ["invoice:INV-1042", "invoice:INV-1043"],
    "claimed_amount_minor": 150000,
    "currency": "KZT",
    "evidence_ids": ["doc:DOC-INV-1042", "doc:DOC-INV-1043", "ledger:L-0001"],
    "explanation": "Два доступных счёта имеют одинаковую сумму и подтверждаются проводкой."
  }]
}
```

`episode_id` совпадает с текущим. `findings` — 0–30 элементов. В каждом все показанные поля обязательны: `claimed_amount_minor` — безопасное целое; `currency` — ISO 4217 из manifest; `evidence_ids` — 1–10 ID; `explanation` — 1–1000 Unicode-кодовых точек. ID следуют разделу 2.

| `type` | Ошибка |
| --- | --- |
| `duplicate_invoice` | дубли счетов |
| `payment_allocation` | пропущенное или частичное распределение платежа |
| `fx_date_or_rate` | неверная дата или курс конвертации |
| `reporting_period` | неверное отнесение к отчётному периоду |

Сопоставление взаимно однозначно. Допуск суммы в v1 — **0** минимальных единиц (решение команды: генератор округляет до сравнения). TP требует совпадения `type`, точного множества `affected_ids`, валюты, суммы в допуске и того, что evidence содержит полный допустимый набор эталона. Несопоставленные и повторные findings — FP; эталон без пары — FN. `explanation` на score не влияет.

`P=TP/(TP+FP)`, `R=TP/(TP+FN)`, `F1=2TP/(2TP+FP+FN)`; при нулевом знаменателе P или R равны 0. При пустых эталоне и findings `F1=1`; при пустом эталоне и непустых findings `F1=0`. Терминальная награда: `clip(2*F1 - 1 - 0.002*inspection_steps, -1, 1)`.

Исследование имеет reward 0. В `open` терминальная награда возвращается в `step.reward`. В `closed` публичный `step.reward` **всегда 0**, включая корректный и некорректный submit; внутренняя награда и диагностика остаются у оценщика после проверки. Внутреннее правило `-1` для некорректного submit сохраняется.

## 6. Изоляция и воспроизводимость

Агент и фронтенд не получают `truth.json`, `provenance.json`, seed, серверные пути, настройки закрытого режима или диагностику matcher. Оценщик работает вне процесса агента. Записи и документы доступны read-only лишь через действия. Нет shell, произвольного SQL, файловой системы, интернета, URL или выполнения кода. Ошибки, `info`, логи, траектории и награда не создают утечек. Агент не меняет seed/config/режим/лимиты.

Одинаковые `(seed, config, environment_version)` и принятые действия воспроизводят данные, переходы и награды; в closed это включает постоянный публичный reward 0.

## 7. HTTP API

Префикс `/api/v1`, JSON и `Content-Type: application/json`. После создания нужен `Authorization: Bearer <episode_token>`; токен даёт доступ только к одному эпизоду. Чужой эпизод возвращает `404`. Регистрации и смены режима в v1 нет.

| Метод и URL | Тело/назначение | Успех |
| --- | --- | --- |
| `POST /episodes` | `{"dataset_profile":"train","company_size":"small"}`; оба обязательны. | `201`: `{episode_token, observation, info}` из reset. |
| `POST /episodes/{episode_id}/steps` | действие раздела 3. | `200`: полный результат step, включая error среды. |
| `GET /episodes/{episode_id}` | публичное состояние без шага. | `200`: `{episode_id, observation, remaining_steps, terminated, truncated}`. |
| `GET /episodes/{episode_id}/trajectory` | публичный экспорт. | `200 application/x-ndjson`. |

Seed и `evaluation_mode` выбирает сервер; они, truth/provenance и диагностика не возвращаются. State содержит только последнее публичное наблюдение.

Транспортные ошибки не доходят до среды и не расходуют шаг: `400 malformed_json`, `401 missing_token`, `403 invalid_token`, `404 episode_not_found`, `409 episode_finalized`, `413 request_too_large`, `415 unsupported_media_type`, `429 rate_limited`, `500 internal_error`. Их тело: `{"error":{"code":"malformed_json","message":"Request body is not valid JSON."}}`. Ошибки действий — это `200` и `observation.kind="error"` либо терминальный результат среды.

## 8. Trajectories

Публичный `trajectories.jsonl` — UTF-8 NDJSON, по одной строке на принятый step, со строгими полями:

```json
{"episode_id":"ep_01JABCDEF1234567890","step":1,"action":{"kind":"list_records","table":"ledger","cursor":null,"limit":25},"observation":{"kind":"records","request":{"kind":"list_records","table":"ledger","cursor":null,"limit":25},"records":[],"next_cursor":null,"remaining_steps":99},"reward":0,"terminated":false,"truncated":false}
```

Там нет seed, truth/provenance, режима, внутреннего score и matcher-диагностики. Закрытый журнал оценщика может их содержать, но не экспортируется и не монтируется агенту.

## 9. Совместимость и ответственность

Совместимы только изменения, сохраняющие reset/step, ID, enum, поля, лимиты, завершение и маршруты. Иначе в том же PR обновляются этот контракт, примеры, версия и тесты. Необязательные поля — только в новой minor-версии без изменения старой семантики; удаление/переименование требуют major.

| Область | Ответственные |
| --- | --- |
| Бэкенд, транспорт, состояние эпизодов, интеграция | Дарий |
| Генератор, семантика среды, matcher, награда, базовые политики, оценка качества | Дарий + Арик |

Арик не получает самостоятельных задач по бэкенду. Обязательный запуск будущей реализации остаётся офлайн, без платных API и GPU.

## 10. Артефакты генератора и учётная политика (добавлено в 1.1.0)

Реализация: issue #2, подробности в [GENERATOR.md](GENERATOR.md).

### 10.1. Файлы эпизода

`<episode_id>/public/`: `manifest.json` (ровно поля manifest из раздела 3), `policy.json` (= `policy_summary`), `ledger.csv`, `invoices.jsonl`, `payments.jsonl`, `fx.csv`. `<episode_id>/private/`: `truth.json`, `provenance.json`; эти файлы видит только оценщик.

Колонки файлов совпадают с кейсом: `ledger.csv` — `row_id, timestamp, account, debit_minor, credit_minor, currency, entity_id, invoice_id, payment_id`; `invoices.jsonl` — `doc_id, invoice_id, entity_id, issued_at, due_at, currency, total_minor, line_items` (позиция: `description, quantity, unit_price_minor, amount_minor`); `payments.jsonl` — `doc_id, payment_id, timestamp, currency, amount_minor, invoice_refs`; `fx.csv` — `date, source_currency, target_currency, rate`. Значения ID в файлах уже канонические (`ledger:…`, `invoice:…`, `payment:…`, `doc:…`, `entity:…`). В наблюдениях раздела 3 среда переименовывает `row_id` и `doc_id` в `id`, не меняя значений; запись курса в наблюдении получает `id` вида `fx:<date>:<source>:<target>`. Отсутствующая ссылка: JSON `null`, в CSV литерал `null`. Все строки книги в базовой валюте. Отрицательный `amount_minor` платежа означает возврат.

Один `(seed, config, environment_version)` даёт байтово одинаковые файлы (UTF-8, LF). На данные влияют только `dataset_profile`, `company_size` и `enabled_issue_types`; `max_steps`, `time_limit_seconds` и `evaluation_mode` данные не меняют.

### 10.2. `policy_summary` / `policy.json`

Все поля обязательны:

| Поле | Значение в v1.1 | Смысл |
| --- | --- | --- |
| `fx_rate_date` | `document_timestamp_local_date` | дата курса — календарная дата документа в часовом поясе компании: у счёта `issued_at`, у платежа `timestamp` |
| `period_boundary` | `inclusive_start_and_end` | документ относится к периоду, если его момент лежит в `[period_start, period_end]` |
| `rounding` | `half_up_to_currency_minor` | как `manifest.rounding_rule`, один раз на конвертируемую сумму |
| `fx_rate_direction` | `target_per_source` | 1 единица `source_currency` = `rate` единиц `target_currency` |
| `ledger_currency` | `base_currency` | все строки книги в базовой валюте |
| `accounts` | объект «код → название» | план счетов эпизода |
| `invoice_posting` | `debit_1200_credit_4000_at_issued_at_local_date_rate` | проводка счёта |
| `payment_posting` | `debit_1000_at_payment_rate_credit_1200_per_allocation_at_invoice_rate_excess_to_2400_difference_to_7900` | проводка платежа |
| `allocation_order` | `invoice_refs_in_order_up_to_outstanding` | платёж гасит счета из `invoice_refs` по порядку, каждый не больше остатка |
| `refunds` | `negative_amount_debit_2400_credit_1000` | возврат ранее нераспределённой суммы |

Причина изменения: без этих правил агент не может отличить законную переплату, разбитый или групповой платёж от ошибки распределения и не знает, по какому курсу гасится дебиторка. Это противоречило бы требованию кейса «нельзя требовать недоступный факт». В 1.0.0 пример `fx_rate_date` был `payment_timestamp_local_date`, но счёт в иностранной валюте тоже пересчитывается, поэтому значение расширено до даты документа.

### 10.3. Эталон: суммы, `affected_ids`, доказательства

Сумма — абсолютное искажение книги в минимальных единицах базовой валюты; валюта — базовая.

| `type` | Цель | Точный `affected_ids` | Сумма |
| --- | --- | --- | --- |
| `duplicate_invoice` | счёт | оба `invoice:*` (оригинал и дубль) | проводка 1200 дубля |
| `payment_allocation` | платёж | `payment:*` и `invoice:*`, который он должен был погасить | недопогашенная сумма (строка 2400 платежа) |
| `fx_date_or_rate` | счёт | строки 1200 и 4000 счёта | разница с пересчётом по политике |
| `fx_date_or_rate` | платёж | строка 1000 платежа | разница с пересчётом по политике |
| `reporting_period` | счёт | все строки проводки счёта | сумма проводки (1200) |
| `reporting_period` | платёж | все строки проводки платежа | строка 1000 |

Допустимые наборы доказательств перечислены в `truth.json.evidence_sets` и описаны в GENERATOR.md; каждый состоит из открытых `doc:*`, `ledger:*`, `fx:*` и сам по себе достаточен для пересчёта суммы. `truth.json`: `{episode_id, issues:[{issue_id, type, target, scenario, affected_ids, expected_amount_minor, currency, evidence_sets}]}`.

### 10.4. Журнал изменений

- **1.1.0**: обязательные поля `policy_summary` (10.2), значение `fx_rate_date` = `document_timestamp_local_date`, точные правила эталона (10.3), формат файлов эпизода (10.1). Публичные поля наблюдений, кроме `policy_summary`, и действия не меняются.
- **1.0.0**: первая согласованная версия.
