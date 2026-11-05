# Участие в разработке

## Установка

```bash
uv venv --python 3.13
uv pip install --python .venv/bin/python -e ".[dev]"
cp .env.example .env
```

## Проверки

```bash
ruff format app tests scripts webhook_sink
ruff check app tests scripts webhook_sink
pytest -m "not integration"
ADQ_INTEGRATION=1 pytest -m integration
```

Интеграционные тесты требуют запущенного Compose-стека и `ADQ_API_URL`.

## Стиль коммитов

Conventional Commits: `feat:`, `fix:`, `test:`, `docs:`, `chore:`, `refactor:`.

## Pull request

- Один PR — одна тема.
- Для изменения поведения добавляй или обновляй тесты.
- При добавлении настройки обновляй `.env.example`.
- Не коммить `.env`, файлы БД, артефакты coverage и логи.
