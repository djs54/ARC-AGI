.PHONY: help smoke smoke-compare smoke-temporal test test-a test-all install temporal-up temporal-down

PYTHON ?= .venv/bin/python
CAMPY_REPO ?= ../hippocampy
CAMPY_MCP_CMD ?= $(CAMPY_REPO)/.venv/bin/python -m campy.adapters.mcp_server

help: ## show this help
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} /^[a-zA-Z_-]+:.*##/ { printf "  %-10s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

smoke: ## live smoke: 1 puzzle, 10 steps, real ARC API + local Ollama
	@CAMPY_MCP_CMD="$(CAMPY_MCP_CMD)" \
	 PYTHONPATH=. $(PYTHON) run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 10

smoke-compare: ## Run v1 and v2 smoke on mock, compare
	@echo "Running v1..."
	@ARC_ARTIFACTS_DIR=artifacts PYTHONPATH=. $(PYTHON) run_single_puzzle.py --max-steps 5 --agent-version=v1 2>/dev/null || true
	@cp artifacts/submission_results_single.json artifacts/submission_results_single.v1.json
	@echo "Running v2..."
	@ARC_ARTIFACTS_DIR=artifacts CAMPY_MCP_CMD="$(CAMPY_MCP_CMD)" PYTHONPATH=. $(PYTHON) run_single_puzzle.py --max-steps 5 --agent-version=v2 2>/dev/null
	@echo "Comparing..."
	@ARC_ARTIFACTS_DIR=artifacts $(PYTHON) -m pytest tests/test_arc4_v1_v2_comparison.py::TestV1vsV2Comparison::test_comparison_summary -v -s

test: ## run the full pytest suite
	$(PYTHON) -m pytest -q

test-a: ## run only the A022-A024 suites
	$(PYTHON) -m pytest -q \
	  tests/test_observability.py \
	  tests/test_plan_registration_idempotent.py \
	  tests/test_exploration_probing.py \
	  tests/test_trace_durability.py

test-all: ## run full test suite baseline
	$(PYTHON) -m pytest tests/ -q

install: ## editable install of sibling brain + this repo
	pip install -e $(CAMPY_REPO) && pip install -e .

temporal-up: ## start local Temporal + Postgres + UI
	docker compose -f docker-compose.temporal.yml up -d

temporal-down: ## stop local Temporal services
	docker compose -f docker-compose.temporal.yml down

smoke-temporal: temporal-up ## full Temporal smoke: start server, run puzzle via Temporal, stop server
	@echo "Waiting for Temporal to be ready..."
	@sleep 5
	@echo "Starting Temporal worker..."
	@ARC_TEMPORAL_ENABLED=1 $(PYTHON) -m agents.arc4.temporal_worker > /tmp/temporal_worker.log 2>&1 &
	@WORKER_PID=$$!; \
	sleep 2; \
	echo "Running puzzle through Temporal..."; \
	PYTHONPATH=. ARC_TEMPORAL_ENABLED=1 $(PYTHON) run_single_puzzle.py --agent-version=v2 --num-puzzles 1 --max-steps 5 --temporal; \
	RESULT=$$?; \
	kill $$WORKER_PID 2>/dev/null || true; \
	$(MAKE) temporal-down; \
	exit $$RESULT

