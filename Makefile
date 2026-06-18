.DEFAULT_GOAL := help

.PHONY: help
help:
	@fgrep -h "##" $(MAKEFILE_LIST) | fgrep -v fgrep | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install: ## Sync the uv-managed virtualenv (creates .venv)
	uv sync

.PHONY: lint
lint: ## Run code linters
	uv run ruff format --check hattori tests
	uv run ruff check hattori tests
	uv run mypy hattori tests/mypy_test.py

.PHONY: fmt format
fmt format: ## Run code formatters
	uv run ruff format hattori tests
	uv run ruff check --fix hattori tests

.PHONY: test
test: ## Run tests
	uv run pytest

.PHONY: test-cov
test-cov: ## Run tests with coverage
	uv run pytest --cov=hattori --cov-report term-missing tests
