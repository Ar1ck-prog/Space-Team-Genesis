# Space Team Genesis

Воспроизводимая офлайн RL-среда для ИИ-аудитора (FinTech-кейс хакатона Space Team). Генератор создаёт согласованные синтетические компании, агент исследует открытые записи через `reset/step`, а изолированный оценщик проверяет выводы и считает награду.

Контракт: [docs/API_CONTRACT.md](docs/API_CONTRACT.md). Условия кейса: [docs/CASE.md](docs/CASE.md). [Результаты прогонов](docs/RESULTS.md). Демо: [docs/DEMO.md](docs/DEMO.md).

## Быстрый запуск

Нужен Python 3.11+; установка пакетов, интернет, GPU и платные API не требуются. Из корня репозитория:

```bash
make check     # тесты, проверка 10 train + 10 dev, интеграционный прогон
make demo      # один dev-эпизод и публичный outputs/trajectories.jsonl
make report    # сводка четырёх политик на train и dev
make serve     # HTTP API на 127.0.0.1:8000
```

Без `make`: `PYTHONPATH=src:. python3 -m space_team_genesis.cli check`. Остальные команды доступны через тот же модуль (`run`, `report`, `serve`).

Для отдельного воспроизводимого прогона:

```bash
PYTHONPATH=src:. python3 -m space_team_genesis.cli run --seed 2013 --profile dev --size small --mode closed --policy reconcile --out outputs/trajectories.jsonl
```

Публичная траектория содержит только действия, наблюдения и доступную агенту награду. В `closed` она всегда равна нулю; P/R/F1 и внутренняя награда выводятся только доверенной локальной командой после завершения эпизода. Oracle доступен только оценщику и не вызывается через HTTP.

## Контейнер

```bash
docker build -t space-team-genesis .
docker run --rm --network none space-team-genesis python -m space_team_genesis.cli check
docker run --rm -p 8000:8000 space-team-genesis
```

Вторую команду можно запускать полностью без сети. Последняя поднимает HTTP API на `/api/v1`. Для локального запуска без контейнера используйте `make serve`.

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/episodes -H 'Content-Type: application/json' -d '{"dataset_profile":"dev","company_size":"small"}'
```

Ответ содержит `episode_id` и `episode_token`. Далее передавайте `Authorization: Bearer <episode_token>` в `POST /api/v1/episodes/{episode_id}/steps`, `GET /api/v1/episodes/{episode_id}` и `GET /api/v1/episodes/{episode_id}/trajectory`. Seed и режим через HTTP не задаются. Открытые эпизоды описаны в [docs/DATASET.md](docs/DATASET.md).

## Состав

- Генератор: `auditgym/`; публичное ядро: `src/space_team_genesis/environment.py`.
- Оценщик, matcher, награда и политики: `auditgym/`; изоляция и сборка компонентов: `src/space_team_genesis/integration.py`.
- HTTP: `src/space_team_genesis/http_api.py`; единые команды: `src/space_team_genesis/cli.py`.

Закрытые `truth`/`provenance` остаются в дочернем процессе оценщика; seed известен только доверенному серверу и оценщику. При одинаковых seed, конфигурации, версии и действиях данные и переходы повторяются. Обучение весов модели не требуется.

**Срок сдачи:** 22 ноября 2026, 23:59 по Астане.

## Лицензия

[MIT](LICENSE)
