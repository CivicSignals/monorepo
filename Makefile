# CivicSignals — developer entrypoints.
# `make dev` is the single command to bring up the local stack (TODO A2).

COMPOSE := docker compose -f infra/docker-compose.dev.yml --env-file .env

# Full-stack e2e overlay: dev stack + LLM_BACKEND=fake + a `seed-e2e` one-shot
# (no vendor key, deterministic fixture gateway). Used by `make e2e-stack`.
COMPOSE_E2E := docker compose -f infra/docker-compose.dev.yml -f infra/docker-compose.e2e.yml --env-file .env

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

.PHONY: seed-e2e
seed-e2e: env ## Migrate, then seed the deterministic e2e routing scenarios (real pipeline, fake LLM; QA-C)
	$(COMPOSE_E2E) run --rm migrate
	$(COMPOSE_E2E) run --rm seed-e2e

.PHONY: e2e-stack
e2e-stack: env ## Bring up the stack (LLM_BACKEND=fake), seed e2e scenarios, run the @fullstack Playwright specs (QA-C)
	# Local convenience target for the full-stack feed e2e. Brings up the dev
	# stack with the fake LLM backend (no vendor key), seeds the deterministic
	# routing scenarios, then runs the externally-started @fullstack project.
	#
	# Needs: docker compose (stack), Node 22 + pnpm 9 with `pnpm install` done,
	# and Playwright's Chromium installed (`pnpm --filter @civicsignals/web exec
	# playwright install chromium`). The web service publishes :3000 and the API
	# :8000 — PLAYWRIGHT_BASE_URL points at the running web app and
	# PLAYWRIGHT_NO_WEBSERVER stops Playwright from booting its own `next dev`.
	# Best-effort/local: the canonical run is .github/workflows/e2e-full-stack.yml.
	$(COMPOSE_E2E) up --build -d
	$(COMPOSE_E2E) run --rm seed-e2e
	PLAYWRIGHT_NO_WEBSERVER=1 PLAYWRIGHT_BASE_URL=http://localhost:3000 \
		pnpm --filter @civicsignals/web run e2e:fullstack

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
	@bash -euo pipefail -c '\
		shopt -s nullglob; \
		recipes=(recipes/*/recipe.yml); \
		if [ $${#recipes[@]} -eq 0 ]; then echo "no recipes to validate"; else \
			uvx --from "check-jsonschema==0.37.2" check-jsonschema \
				--schemafile packages/recipe-schema/schema/recipe.schema.json \
				"$${recipes[@]}"; \
		fi'
	cd apps/api && uv run python -m civicsignals_api.modules.recipes.cli
