.PHONY: help smoke smoke-compare smoke-temporal test test-a test-all install temporal-up temporal-down check-compliance compliance-history

PYTHON ?= .venv/bin/python
CAMPY_REPO ?= ../hippocampy
CAMPY_MCP_CMD ?= $(CAMPY_REPO)/.venv/bin/python -m campy.adapters.mcp_server

help: ## show this help
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} /^[a-zA-Z_-]+:.*##/ { printf "  %-10s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

smoke: ## live smoke: 1 puzzle, 10 steps, real ARC API + local Ollama
	@CAMPY_MCP_CMD="$(CAMPY_MCP_CMD)" \
	 PYTHONPATH=. $(PYTHON) run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 10

smoke-compare: ## RETIRED (A148): v1 was archived to archive/agents-arc3/; see that dir for the old comparison harness
	@echo "smoke-compare is retired (A148) — agents/arc3 (v1) was archived to archive/agents-arc3/."
	@echo "There is no live v1 to compare against; v2 (agents/arc4) is the only supported agent."
	@echo "The old comparison test lives at archive/agents-arc3/tests/test_arc4_v1_v2_comparison.py for reference."

test: ## run the full pytest suite
	$(PYTHON) -m pytest -q

test-a: ## fast pre-commit subset: observability, trace durability, import boundary, cycle policy, shift-a boundary
	$(PYTHON) -m pytest -q \
	  tests/test_observability.py \
	  tests/test_trace_durability.py \
	  tests/test_import_boundary.py \
	  tests/test_a140_cycle_policy.py \
	  tests/test_a222_shift_a_static_boundary.py

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

check-compliance: ## exit non-zero if the most recent smoke trace recorded a Shift-B violation
	$(PYTHON) scripts/check_compliance_violations.py

compliance-history: ## record the latest smoke trace's compliance report into the trend history
	$(PYTHON) scripts/graph_compliance_report.py artifacts/agent_execution_trace.json --append-history
