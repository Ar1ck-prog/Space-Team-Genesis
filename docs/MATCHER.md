# Оценщик, matcher и награда

Issue #4. Код: [auditgym/submission.py](../auditgym/submission.py) (структура submit), [auditgym/matcher.py](../auditgym/matcher.py) (сопоставление и метрики), [auditgym/reward.py](../auditgym/reward.py) (награда), [auditgym/evaluator.py](../auditgym/evaluator.py) (оценка эпизода). Правила взяты из [API_CONTRACT.md](API_CONTRACT.md), разделы 2, 4 и 5; этот документ их не меняет, а уточняет и показывает на примерах.

```python
from auditgym.evaluator import Evaluator
ev = Evaluator.from_episode(episode, mode="closed")      # или Evaluator.from_dir(path, mode)
res = ev.score_submission(findings_doc, inspection_steps)  # ровно один раз за эпизод
# или ev.score_truncation(inspection_steps, "steps" | "time")
ev.public_reward()   # единственное значение для политики: награда в open, всегда 0 в closed
ev.result()          # полный приватный результат: P, R, F1, TP/FP/FN, пары, причина отказа
```

## Структура submit

Неверная структура завершает эпизод с внутренней наградой **−1**. Проверка не смотрит на эталон, поэтому коды ошибок ничего не говорят о правильном ответе.

- Объект ровно с ключами `episode_id` и `findings`; `episode_id` совпадает с текущим; `findings` — массив из 0–30 элементов.
- Каждый вывод ровно с полями `type, affected_ids, claimed_amount_minor, currency, evidence_ids, explanation`.
- `type` — один из четырёх; `claimed_amount_minor` — целое в безопасном диапазоне JS (bool, дробь и строка не подходят); `currency` — код из `manifest.currency_decimals`, регистр точный; `explanation` — 1–1000 кодовых точек Unicode.
- ID канонические: ASCII, 1–128 символов, без пробелов, префикс `ledger:`, `invoice:`, `payment:`, `doc:` (в локальной части нет `:`) или ровно форма `fx:<YYYY-MM-DD>:<SRC>:<TGT>` с кодами из заглавных букв. Существование даты не проверяется: такой ID корректен по структуре и просто ничему не соответствует. Регистр не нормализуется.
- `affected_ids`: не пусто, без `doc:*`, без повторов; форма по типу: `duplicate_invoice` — не меньше двух ID, все `invoice:*`; `payment_allocation` — ровно один `payment:*` и хотя бы один `invoice:*`; `fx_date_or_rate` и `reporting_period` — хотя бы один `ledger:*`.
- `evidence_ids`: 1–10 ID любого пространства, без повторов.
- Канонизация массивов: сортировка. Повтор — ошибка структуры, поэтому канонизация ничего не склеивает.
- **Открытый вопрос к среде.** Валидатор среды (`src/space_team_genesis/environment.py`, PR #10) требует, чтобы массивы уже пришли отсортированными, и иначе возвращает `invalid_submission`; оценщик несортированный массив сортирует сам. В связке среда проверяет первой, поэтому сейчас фактически действует более строгое правило. Контракт допускает оба прочтения, нужно выбрать одно (вектор `affected_unsorted`).

## Сопоставление

Вывод **подходит** эталонной ошибке, если совпадают тип, точное множество `affected_ids`, валюта, сумма (допуск **0**), а `evidence_ids` содержит хотя бы один допустимый набор доказательств целиком. Лишние доказательства разрешены. Части двух разных наборов полным набором не считаются. Псевдонимов нет: `invoice:*` не заменяет `doc:*`.

Пары выбираются как **максимальное взаимно однозначное паросочетание** (дополняющие пути). Выводы перебираются в порядке отправки, эталон — по `issue_id`, поэтому результат детерминирован. Все максимальные паросочетания одного размера, так что этот выбор не влияет на TP/FP/FN и награду. Жадный подбор здесь не годится: вектор `ambiguous_max_matching` показывает случай, где он нашёл бы 1 пару вместо 2. Несопоставленные и повторные выводы — FP, эталон без пары — FN, текст `explanation` на счёт не влияет.

## Метрики и награда

`P=TP/(TP+FP)`, `R=TP/(TP+FN)`, `F1=2TP/(2TP+FP+FN)`; при нулевом знаменателе P или R равны 0. Пустые эталон и выводы дают `F1=1`; пустой эталон при непустых выводах — `F1=0`.

`reward = clip(2·F1 − 1 − 0.002·inspection_steps, −1, 1)`. `inspection_steps` — принятые шаги до submit (0–99).

**Обрезка по лимиту шагов или времени.** По кейсу итоговый ответ тогда пустой. Оценщик считает пустой список выводов с фактическим числом принятых шагов (0–100); отправленные ранее выводы не оцениваются. На чистой книге это даёт `1 − 0.002·шаги`, на книге с ошибками −1.

**Open и closed.** В open политика получает терминальную награду (в том числе −1 за неверную структуру). В closed `public_reward()` всегда 0, а результат и диагностика остаются в `ev.result()` и `ev.log`.

### Измеренное свойство общей формулы

Цена одного пропуска при `n` эталонных ошибках — `2 − 4n/(2n+1)`, а цена шага — 0.002:

| n | 1 | 2 | 3 | 4 | 5 | 6 | 8 | 10 | 12 |
|---|---|---|---|---|---|---|---|---|---|
| пропуск стоит шагов | 333 | 200 | 143 | 111 | 91 | 77 | 59 | 48 | 40 |

При одинаковом числе шагов полный ответ всегда лучше неполного. Но начиная с 5 ошибок быстрый ответ без одной находки может обойти полный ответ, на который ушло 99 шагов. Это свойство общей функции из кейса, matcher его не меняет; при желании можно добавить отдельную экспериментальную награду и показывать её отдельно.

## Изоляция (что осталось Дарию, issue #4 и #3)

Ядро не знает про процессы и сеть. Запуск оценщика вне процесса агента, загрузка закрытого эталона только на его стороне и передача `public_reward()` в `step` делаются в интеграции со средой. Эталон хранится только внутри `Evaluator`; в публичном результате нет ничего, кроме числа награды.

## Таблица ожидаемых результатов

Векторы: [tests/fixtures/matcher_vectors.json](../tests/fixtures/matcher_vectors.json), прогон: `tests/test_matcher_vectors.py`. Эталон `T`: три ошибки (`duplicate_invoice`, `fx_date_or_rate`, `payment_allocation`) в KZT, валюты манифеста KZT и USD. Ожидаемые TP/FP/FN заданы вручную; F1 и награда посчитаны по формулам контракта.

| Вектор | Что проверяет | Шаги | Структура | TP | FP | FN | F1 | Награда |
|---|---|---|---|---|---|---|---|---|
| `all_correct` | full correct answer with evidence | 20 | верна | 3 | 0 | 0 | 1.000 | +0.960 |
| `all_correct_zero_steps` | maximum reward | 0 | верна | 3 | 0 | 0 | 1.000 | +1.000 |
| `all_correct_99_steps` | step cost only slightly separates equal answers | 99 | верна | 3 | 0 | 0 | 1.000 | +0.802 |
| `silence_on_dirty` | silence on erroneous books | 5 | верна | 0 | 0 | 3 | 0.000 | -1.000 |
| `silence_on_clean` | empty truth and empty findings: F1=1 | 5 | верна | 0 | 0 | 0 | 1.000 | +0.990 |
| `accusation_on_clean` | empty truth with findings: F1=0 | 5 | верна | 0 | 1 | 0 | 0.000 | -1.000 |
| `one_of_three` | partial recall | 10 | верна | 1 | 0 | 2 | 0.500 | -0.020 |
| `two_of_three` | one issue missed | 10 | верна | 2 | 0 | 1 | 0.800 | +0.580 |
| `correct_plus_false` | one FP among correct findings | 10 | верна | 3 | 1 | 0 | 0.857 | +0.694 |
| `duplicate_finding` | a repeated finding is a FP, no extra credit | 10 | верна | 3 | 1 | 0 | 0.857 | +0.694 |
| `triple_finding` | three copies of one finding | 10 | верна | 1 | 2 | 2 | 0.333 | -0.353 |
| `order_irrelevant` | submission order does not matter | 10 | верна | 3 | 0 | 0 | 1.000 | +0.980 |
| `clip_floor` | reward never goes below -1 | 99 | верна | 0 | 1 | 3 | 0.000 | -1.000 |
| `wrong_type` | type must match | 0 | верна | 0 | 1 | 3 | 0.000 | -1.000 |
| `amount_plus_one` | tolerance is 0 | 0 | верна | 0 | 1 | 3 | 0.000 | -1.000 |
| `amount_minus_one` | tolerance is 0 | 0 | верна | 0 | 1 | 3 | 0.000 | -1.000 |
| `other_manifest_currency` | currency must match the truth | 0 | верна | 0 | 1 | 3 | 0.000 | -1.000 |
| `affected_subset` | affected set must be exact | 0 | верна | 0 | 1 | 3 | 0.000 | -1.000 |
| `affected_superset` | affected set must be exact | 0 | верна | 0 | 1 | 3 | 0.000 | -1.000 |
| `affected_unsorted` | arrays are canonicalized by sorting | 0 | верна | 1 | 0 | 2 | 0.500 | +0.000 |
| `affected_case_changed_local` | ids are case-sensitive | 0 | верна | 0 | 1 | 3 | 0.000 | -1.000 |
| `guessed_ids` | plausible but wrong ids | 0 | верна | 0 | 1 | 3 | 0.000 | -1.000 |
| `evidence_alternative_set` | any acceptable set is enough | 0 | верна | 1 | 0 | 2 | 0.500 | +0.000 |
| `evidence_extra_ids` | extra evidence is allowed | 0 | верна | 1 | 0 | 2 | 0.500 | +0.000 |
| `evidence_partial` | an incomplete set does not count | 0 | верна | 0 | 1 | 3 | 0.000 | -1.000 |
| `evidence_mixed_sets` | pieces of two sets do not make a full set | 0 | верна | 0 | 1 | 3 | 0.000 | -1.000 |
| `evidence_alias_not_accepted` | invoice:* does not replace doc:* | 0 | верна | 0 | 1 | 3 | 0.000 | -1.000 |
| `evidence_unsupported` | a single document is not enough | 0 | верна | 0 | 1 | 3 | 0.000 | -1.000 |
| `explanation_does_not_matter` | persuasive text gives nothing | 0 | верна | 1 | 0 | 2 | 0.500 | +0.000 |
| `ambiguous_max_matching` | greedy would match 1; the maximum matching finds 2 | 0 | верна | 2 | 0 | 0 | 1.000 | +1.000 |
| `ambiguous_one_finding` | one finding satisfies only one issue | 0 | верна | 1 | 0 | 1 | 0.667 | +0.333 |
| `wrong_episode` | episode_id must match | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `missing_findings_key` | both keys are required | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `extra_top_key` | no extra keys | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `findings_not_array` | findings must be an array | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `submission_not_object` | findings must be an object | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `too_many_findings` | 31 findings | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `finding_extra_key` | no extra keys in a finding | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `finding_missing_key` | all fields are required | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `unknown_type` | type enum | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `repeated_affected` | repeats are invalid | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `repeated_evidence` | repeats are invalid | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `doc_in_affected` | doc:* is forbidden in affected_ids | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `bad_prefix` | unknown namespace | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `prefix_case` | namespace is case-sensitive | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `duplicate_one_invoice` | duplicate_invoice needs >= 2 invoice ids | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `allocation_no_payment` | payment_allocation needs one payment id | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `allocation_two_payments` | payment_allocation needs exactly one payment id | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `fx_without_ledger` | fx_date_or_rate needs a ledger id | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `empty_evidence` | 1..10 evidence ids | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `eleven_evidence` | 1..10 evidence ids | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `empty_affected` | affected_ids cannot be empty | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `amount_float` | integer required | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `amount_bool` | bool is not an integer | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `amount_string` | integer required | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `amount_unsafe` | outside the JS safe range | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `currency_not_in_manifest` | currency must be in the manifest | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `currency_lowercase` | currency codes are exact | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `explanation_empty` | 1..1000 code points | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `explanation_1001` | 1..1000 code points | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `duplicate_with_ledger_id` | duplicate_invoice affected_ids are invoice ids only | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `fx_short_date` | fx id must have the form fx:<YYYY-MM-DD>:<SRC>:<TGT> | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `fx_lowercase_currency` | fx id currencies are upper-case | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `id_with_space` | no whitespace | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `id_non_ascii` | ASCII only | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `id_too_long` | at most 128 characters | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `id_colon_in_local` | the local part has no colon | 7 | неверна | 0 | 0 | 3 | 0.000 | -1.000 |
| `thirty_findings` | 30 findings are allowed | 0 | верна | 1 | 29 | 2 | 0.061 | -0.879 |
| `explanation_1000_code_points` | 1000 code points (non-BMP) are allowed | 0 | верна | 1 | 0 | 2 | 0.500 | +0.000 |
| `fx_nonexistent_date` | a well-formed fx id for a non-existent date is valid structure, it just matches nothing | 0 | верна | 0 | 1 | 3 | 0.000 | -1.000 |
| `id_128_chars` | exactly 128 characters is allowed (here a wrong id, so FP) | 0 | верна | 0 | 1 | 3 | 0.000 | -1.000 |
