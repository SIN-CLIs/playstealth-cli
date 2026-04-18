# =============================================================================
# HeyPiggy Vision Worker — Developer convenience targets.
# =============================================================================
#
# Run `make help` for an overview.
# -----------------------------------------------------------------------------

.PHONY: help install install-dev lint format typecheck test test-cov \
        security audit secrets pre-commit clean run docker-build docker-run \
        ci all

PYTHON ?= python3
PIP    ?= $(PYTHON) -m pip

help: ## Show this help.
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage: make \033[36m<target>\033[0m\n\nTargets:\n"} \
	     /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 }' \
	     $(MAKEFILE_LIST)

# --- Install ---------------------------------------------------------------

install: ## Install runtime dependencies.
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

install-dev: ## Install runtime + dev dependencies + pre-commit hooks.
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt -r requirements-dev.txt
	$(PIP) install -e .
	pre-commit install --install-hooks

# --- Lint / format ---------------------------------------------------------

lint: ## Run ruff lint on the codebase.
	ruff check .

format: ## Auto-format with ruff.
	ruff format .
	ruff check --fix .

typecheck: ## Run mypy in strict mode on worker/.
	mypy worker

# --- Tests -----------------------------------------------------------------

test: ## Run the test suite.
	pytest

test-cov: ## Run tests with coverage (fails under 80%).
	pytest --cov=worker --cov-branch --cov-report=term-missing --cov-report=xml --cov-report=html

# --- Security --------------------------------------------------------------

security: ## Run bandit against worker/.
	bandit -c pyproject.toml -r worker

audit: ## Audit installed Python dependencies for known CVEs.
	pip-audit --strict -r requirements.txt

secrets: ## Scan working tree for committed secrets.
	detect-secrets scan --baseline .secrets.baseline

# --- Orchestration ---------------------------------------------------------

pre-commit: ## Run all pre-commit hooks on all files.
	pre-commit run --all-files

ci: lint typecheck test-cov security audit ## Full CI pipeline (lint + types + tests + security).

all: format ci ## Local "full pass" — format then run CI.

# --- Run -------------------------------------------------------------------

run: ## Run the worker via the new CLI entrypoint.
	$(PYTHON) -m worker

run-legacy: ## Run the worker via the backward-compat shim.
	$(PYTHON) heypiggy_vision_worker.py

# --- Docker ----------------------------------------------------------------

docker-build: ## Build the production Docker image.
	docker build -t heypiggy-worker:latest .

docker-run: ## Run the Docker image with env vars from .env.
	docker run --rm --env-file .env heypiggy-worker:latest

# --- Housekeeping ----------------------------------------------------------

clean: ## Remove caches and build artifacts.
	find . -type d -name "__pycache__" -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage .coverage_html coverage.xml build dist *.egg-info
