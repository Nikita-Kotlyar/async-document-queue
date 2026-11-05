# Contributing

## Setup

```bash
uv venv --python 3.13
uv pip install --python .venv/bin/python -e ".[dev]"
cp .env.example .env
```

## Checks

```bash
ruff format app tests scripts webhook_sink
ruff check app tests scripts webhook_sink
pytest -m "not integration"
ADQ_INTEGRATION=1 pytest -m integration
```

Integration tests require the Compose stack and `ADQ_API_URL`.

## Commit style

Conventional Commits: `feat:`, `fix:`, `test:`, `docs:`, `chore:`, `refactor:`.

## Pull requests

- Keep one topic per pull request.
- Add or update tests for behavior changes.
- Update `.env.example` when adding configuration.
- Do not commit `.env`, database files, coverage artifacts or generated logs.
