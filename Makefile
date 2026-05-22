# CivicSignals — developer entrypoints.
# `make dev` is the single command to bring up the local stack (TODO A2).

COMPOSE := docker compose -f infra/docker-compose.dev.yml --env-file .env

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

.PHONY: env
env: ## Create .env from .env.example if missing
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")

.PHONY: dev
dev: env ## Bring up the full local stack (Postgres, Redis, MinIO, Mailpit, PgBouncer, api, web, workers)
	$(COMPOSE) up --build

.PHONY: dev-detached
dev-detached: env ## Same as `dev`, detached
	$(COMPOSE) up --build -d

.PHONY: down
down: ## Stop the local stack
	$(COMPOSE) down

.PHONY: clean
clean: ## Stop the stack and remove volumes (DESTROYS local data)
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Tail stack logs
	$(COMPOSE) logs -f

.PHONY: migrate
migrate: ## Apply Alembic migrations inside the api container
	$(COMPOSE) exec api alembic upgrade head

.PHONY: seed
seed: ## Seed a demo workspace with synthetic signals (TODO A2)
	$(COMPOSE) exec api python -m civicsignals_api.scripts.seed_demo

.PHONY: lint
lint: ## Lint everything via Turborepo
	pnpm lint

.PHONY: test
test: ## Run all tests via Turborepo
	pnpm test

.PHONY: typecheck
typecheck: ## Type-check everything via Turborepo
	pnpm typecheck
