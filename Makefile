.DEFAULT_GOAL := help

.PHONY: help
help:
	@fgrep -h "##" $(MAKEFILE_LIST) | fgrep -v fgrep | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install: ## Install dependencies
	flit install --deps develop --symlink

.PHONY: lint
lint: ## Run code linters
	ruff format --check hattori tests
	ruff check hattori tests
	mypy hattori

.PHONY: fmt format
fmt format: ## Run code formatters
	ruff format hattori tests
	ruff check --fix hattori tests

.PHONY: test
test: ## Run tests
	pytest .

.PHONY: test-cov
test-cov: ## Run tests with coverage
	pytest --cov=hattori --cov-report term-missing tests

.PHONY: docs
docs: ## Serve documentation locally
	pip install -r docs/requirements.txt
	cd docs && mkdocs serve -a localhost:8090
