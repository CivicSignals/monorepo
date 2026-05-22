# @civicsignals/api

FastAPI modulith + Celery workers. Async SQLAlchemy 2.0, Pydantic v2, Alembic.
Python 3.12, managed by [`uv`](https://docs.astral.sh/uv/). Lint/format with
`ruff`, types with `mypy`, tests with `pytest`.

## Develop

```bash
uv sync                                    # create venv + install deps
uv run uvicorn civicsignals_api.main:app --reload --port 8000
```

OpenAPI is served at `/api/v1/openapi.json`, Swagger UI at `/docs`.

## Process model (doc 18 §6.1)

The same image runs as one of six processes, selected by the container command:

| Command          | Role                                  |
|------------------|---------------------------------------|
| `api`            | `uvicorn civicsignals_api.main:app`   |
| `worker_ingest`  | `celery ... worker -Q ingest`         |
| `worker_extract` | `celery ... worker -Q extract`        |
| `worker_score`   | `celery ... worker -Q score`          |
| `worker_notify`  | `celery ... worker -Q notify`         |
| `scheduler`      | `celery ... beat` (leader-elected)    |

## Module layout (doc 06 §3)

Each module under `src/civicsignals_api/modules/<name>/` exposes `services.py`,
`models.py`, `schemas.py`, `routes.py`, `tasks.py`, and `tests/`. No module
imports another's internals — cross-module calls go through `services.py`.

## Migrations

```bash
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head
```
