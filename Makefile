# ecomsbd developer commands.
.DEFAULT_GOAL := help
.PHONY: help setup services backend worker migrate revision test test-backend \
        test-flutter lint format typecheck check flutter-app codegen clean \
        config-check

BACKEND := backend
FLUTTER := apps/mobile_flutter
PY      := $(BACKEND)/.venv/Scripts/python.exe

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Create the backend venv and install both toolchains
	cd $(BACKEND) && python -m venv .venv && .venv/Scripts/python.exe -m pip install -e ".[dev]"
	cd $(FLUTTER) && flutter pub get && dart run build_runner build

services: ## Start PostgreSQL and Redis
	docker compose up -d postgres redis

backend: ## Run the API with reload
	cd $(BACKEND) && .venv/Scripts/python.exe -m uvicorn app.main:app --reload

worker: ## Run the ARQ worker (requires REDIS_URL)
	cd $(BACKEND) && .venv/Scripts/python.exe -m arq app.worker.main.WorkerSettings

migrate: ## Apply migrations
	cd $(BACKEND) && .venv/Scripts/python.exe -m alembic upgrade head

revision: ## Draft a migration:  make revision m="description"
	cd $(BACKEND) && .venv/Scripts/python.exe -m alembic revision --autogenerate -m "$(m)"

codegen: ## Regenerate the Drift database code
	cd $(FLUTTER) && dart run build_runner build

flutter-app: ## Run the app against a local backend
	cd $(FLUTTER) && flutter run --dart-define=ECOMSBD_API_BASE_URL=http://10.0.2.2:8000

test-backend: ## Backend tests
	cd $(BACKEND) && .venv/Scripts/python.exe -m pytest -q

test-flutter: ## Flutter tests
	cd $(FLUTTER) && flutter test

test: test-backend test-flutter ## All tests

lint: ## Lint both codebases
	cd $(BACKEND) && .venv/Scripts/python.exe -m ruff check app tests migrations
	cd $(FLUTTER) && flutter analyze

format: ## Format both codebases
	cd $(BACKEND) && .venv/Scripts/python.exe -m ruff format app tests migrations
	cd $(FLUTTER) && dart format lib test

typecheck: ## Strict type check the backend
	cd $(BACKEND) && .venv/Scripts/python.exe -m mypy

check: lint typecheck test ## Everything CI runs

config-check: ## Check a production env file:  make config-check f=.env.production.local
	cd $(BACKEND) && .venv/Scripts/python.exe -m app.check_production_config \
		--env-file ../$(or $(f),.env.production.local) --env production

clean: ## Remove build and cache artefacts
	cd $(BACKEND) && rm -rf .pytest_cache .mypy_cache .ruff_cache
	cd $(FLUTTER) && flutter clean
