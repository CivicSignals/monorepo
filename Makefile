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

.PHONY: config
config: env ## Validate the dev compose file (renders the fully-resolved config)
	$(COMPOSE) config

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
migrate: env ## Apply Alembic migrations (one-shot container; `make dev` already runs this on startup)
	$(COMPOSE) run --rm migrate

.PHONY: seed
seed: env ## Seed a demo workspace with synthetic signals (idempotent; TODO A2)
	$(COMPOSE) run --rm api seed

.PHONY: lint
lint: ## Lint everything via Turborepo
	pnpm lint

.PHONY: test
test: ## Run all tests via Turborepo
	pnpm test

.PHONY: typecheck
typecheck: ## Type-check everything via Turborepo
	pnpm typecheck

.PHONY: notice
notice: ## Regenerate NOTICE.md from the API runtime dependency tree (TODO A3)
	./scripts/gen-notice.sh

.PHONY: check-notice
check-notice: ## Fail if NOTICE.md is stale or a disallowed license appears (TODO A3)
	./scripts/gen-notice.sh --check

.PHONY: check-recipes
check-recipes: ## Validate recipes against the JSON Schema + replay golden fixtures (TODO A3)
	uvx --from check-jsonschema check-jsonschema \
		--schemafile packages/recipe-schema/schema/recipe.schema.json \
		recipes/*/recipe.yml
	python3 scripts/replay_fixtures.py recipes
