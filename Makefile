.PHONY: help smoke test test-a install

PYTHON ?= .venv/bin/python
CAMPY_REPO ?= ../sidequests-brain
CAMPY_MCP_CMD ?= $(CAMPY_REPO)/.venv/bin/python -m campy.adapters.mcp_server

help: ## show this help
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} /^[a-zA-Z_-]+:.*##/ { printf "  %-10s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

smoke: ## live smoke: 1 puzzle, 10 steps, real ARC API + local Ollama
	@CAMPY_MCP_CMD="$(CAMPY_MCP_CMD)" \
	 PYTHONPATH=. $(PYTHON) run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 10

test: ## run the full pytest suite
	$(PYTHON) -m pytest -q

test-a: ## run only the A022-A024 suites
	$(PYTHON) -m pytest -q \
	  tests/test_observability.py \
	  tests/test_plan_registration_idempotent.py \
	  tests/test_exploration_probing.py \
	  tests/test_trace_durability.py

install: ## editable install of sibling brain + this repo
	pip install -e $(CAMPY_REPO) && pip install -e .
