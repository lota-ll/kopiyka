.DEFAULT_GOAL := help
COMPOSE := docker compose -f docker/compose.dev.yaml
export KOPIYKA_MIGRATION_DATABASE_URL ?= postgresql+asyncpg://postgres:devpass@localhost:5432/kopiyka

.PHONY: help
help: ## Показати цю довідку
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: up
up: ## Підняти локальний стек
	$(COMPOSE) up -d --build

.PHONY: down
down: ## Зупинити стек (дані лишаються)
	$(COMPOSE) down

.PHONY: nuke
nuke: ## Зупинити стек і видалити дані
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Логи API
	$(COMPOSE) logs -f api

.PHONY: migrate
migrate: ## Застосувати міграції
	$(COMPOSE) exec -T api alembic upgrade head

.PHONY: seed
seed: ## Створити демо-household і глобальні категорії
	$(COMPOSE) exec -T api python -m scripts.seed

.PHONY: test
test: ## Тести без БД
	PYTHONPATH=src pytest -q

.PHONY: test-db
test-db: ## Усі тести, включно з RLS (потрібен піднятий стек)
	KOPIYKA_TEST_DATABASE_URL=postgresql+asyncpg://kopiyka_app:devpass@localhost:5432/kopiyka \
		PYTHONPATH=src pytest -q

.PHONY: lint
lint: ## ruff + mypy
	ruff check src tests
	ruff format --check src tests
	mypy src

.PHONY: fmt
fmt: ## Автоформатування
	ruff check --fix src tests
	ruff format src tests

.PHONY: parse
parse: ## Розібрати виписку: make parse FILE=~/statement.csv
	PYTHONPATH=src python -m kopiyka.parsers.cli $(FILE) --stats
