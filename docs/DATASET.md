# Открытые эпизоды: 10 train + 10 dev

Issue #8, часть Арика: контроль разнообразия набора. Код: [auditgym/dataset.py](../auditgym/dataset.py), манифесты: [datasets/train.json](../datasets/train.json) и [datasets/dev.json](../datasets/dev.json), файлы: `episodes/<split>/<episode_id>/public/`.

## Как устроен набор

В каждом наборе 10 **слотов**, то есть описаний того, что должно быть в эпизоде («чистая крупная компания», «платёж на границе периода при UTC−» и т. п.). Подбор идёт по seed от 1000 (train) и от 2000 (dev) и берёт первый seed, эпизод которого через публичный `generate(seed, config)` удовлетворяет слоту и проходит независимую проверку. Seed и config записаны в манифест, поэтому `reset(seed, config)` среды восстанавливает те же книги. Явный план генератора не используется.

- `dataset_profile` совпадает с набором, seed train и dev не пересекаются.
- В репозитории только `public/`. Seed открытых наборов публичны, так что `truth.json` и `provenance.json` доверенный код восстанавливает командой ниже; в репозиторий они не кладутся (чек-лист #8: артефакты без закрытого эталона), `.gitignore` это страхует.
- `episode_id` считается с публичным ключом; при заданном `GENESIS_EPISODE_KEY` команды набора откажутся работать.

```bash
python -m auditgym.dataset check                                   # перегенерировать и сверить побайтово + критерии
python -m auditgym.dataset build                                    # пересобрать episodes/*/public
python -m auditgym.dataset build --with-private --out /tmp/eps      # + эталон, только для доверенного кода, вне репозитория
python -m auditgym.dataset report                                   # таблица ниже
python -m auditgym.dataset select                                   # заново подобрать seed (только при изменении слотов)
```

## Критерии разнообразия (проверяет `check` и `tests/test_dataset.py`)

В каждом наборе: ровно 10 эпизодов; не меньше 2 чистых компаний; не меньше 3 эпизодов со взаимодействующими ошибками; все три размера компании; каждый тип ошибки; оба взаимодействующих сценария.

По двум наборам вместе: каждый тип ошибки не меньше 4 раз; у `fx_date_or_rate` и `reporting_period` встречаются обе цели (счёт и платёж); не меньше 2 базовых валют и 4 часовых поясов; граничный сценарий при положительном и при отрицательном смещении UTC; размеры в пределах кейса (200–2000 проводок, 20–200 документов, до 12 ошибок).

Кроме того, каждый эпизод проходит все независимые проверки генератора (`auditgym/verify.py`: схема, баланс, чистая книга, достаточность доказательств, решаемость по открытым данным, утечки), а файлы в репозитории побайтово совпадают с перегенерацией.

## Состав

Итого: 4 чистые компании, 8 эпизодов со взаимодействующими ошибками, 3 базовые валюты, 7 часовых поясов (в том числе +05:30), от 266 до 1493 проводок.

| Набор | Слот | Seed | Размер | База | Часовой пояс | Проводок | Документов | Ошибок | Типы | Сценарии |
|---|---|---|---|---|---|---|---|---|---|---|
| train | `clean_small` | 1008 | small | USD | Asia/Tokyo (+09:00) | 344 | 67 | 0 | — | — |
| train | `clean_medium` | 1000 | medium | USD | America/Lima (-05:00) | 624 | 97 | 0 | — | — |
| train | `two_types_small` | 1002 | small | USD | Asia/Dubai (+04:00) | 305 | 70 | 4 | dup×1, alloc×3 | — |
| train | `fx_only_small` | 1004 | small | USD | Asia/Almaty (+05:00) | 371 | 61 | 4 | fx×4 | — |
| train | `period_only_medium` | 1003 | medium | KZT | Asia/Tokyo (+09:00) | 769 | 88 | 5 | period×5 | — |
| train | `interaction_fx_invoice_medium` | 1012 | medium | USD | America/Lima (-05:00) | 730 | 108 | 4 | dup×1, fx×2, alloc×1 | fx_invoice_partial |
| train | `interaction_boundary_plus_small` | 1010 | small | EUR | Asia/Kolkata (+05:30) | 299 | 58 | 5 | dup×2, fx×1, alloc×1, period×1 | boundary_payment_fx_partial |
| train | `interaction_boundary_minus_medium` | 1054 | medium | EUR | America/Bogota (-05:00) | 619 | 102 | 4 | fx×1, alloc×2, period×1 | boundary_payment_fx_partial |
| train | `mixed_large` | 1015 | large | USD | Europe/Istanbul (+03:00) | 1422 | 155 | 8 | dup×3, fx×1, alloc×2, period×2 | — |
| train | `both_interactions_large` | 1005 | large | KZT | Asia/Tokyo (+09:00) | 1448 | 153 | 9 | dup×1, fx×5, alloc×2, period×1 | boundary_payment_fx_partial, fx_invoice_partial |
| dev | `clean_small` | 2003 | small | KZT | Asia/Almaty (+05:00) | 281 | 57 | 0 | — | — |
| dev | `clean_large` | 2001 | large | KZT | Asia/Tokyo (+09:00) | 1303 | 143 | 0 | — | — |
| dev | `two_types_small` | 2006 | small | USD | America/Lima (-05:00) | 379 | 71 | 4 | fx×1, period×3 | — |
| dev | `duplicate_only_medium` | 2000 | medium | EUR | America/Sao_Paulo (-03:00) | 665 | 92 | 2 | dup×2 | — |
| dev | `allocation_only_small` | 2002 | small | KZT | America/Bogota (-05:00) | 289 | 52 | 4 | alloc×4 | — |
| dev | `interaction_fx_invoice_small` | 2007 | small | EUR | America/Lima (-05:00) | 288 | 54 | 4 | fx×1, alloc×2, period×1 | fx_invoice_partial |
| dev | `interaction_boundary_plus_medium` | 2010 | medium | USD | Europe/Istanbul (+03:00) | 639 | 115 | 3 | dup×1, fx×1, alloc×1 | boundary_payment_fx_partial |
| dev | `interaction_boundary_minus_small` | 2013 | small | EUR | America/Sao_Paulo (-03:00) | 266 | 57 | 5 | dup×2, fx×1, alloc×1, period×1 | boundary_payment_fx_partial |
| dev | `mixed_medium` | 2057 | medium | KZT | America/Sao_Paulo (-03:00) | 764 | 95 | 5 | dup×1, fx×1, alloc×2, period×1 | — |
| dev | `both_interactions_large` | 2037 | large | USD | America/Sao_Paulo (-03:00) | 1493 | 148 | 11 | dup×2, fx×3, alloc×6 | boundary_payment_fx_partial, fx_invoice_partial |

Обозначения: dup — `duplicate_invoice`, alloc — `payment_allocation`, fx — `fx_date_or_rate`, period — `reporting_period`. Ошибки сценариев входят в счёт типов.
