# A222 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Shift-A "no LLM in deterministic phases" principle a real, CI-enforced, per-PR gate (mirroring how `tests/test_import_boundary.py` already enforces the MCP-seam principle), and make the judgment-based parts of the `arc-graph-engineering-review` skill a visible, required-by-convention section on every PR touching ARC runtime code.

**Architecture:** Two independent, additive mechanisms — a new static-analysis pytest file added to the existing `test-a` required-status-check subset, and a new GitHub PR template file plus a `CLAUDE.md` wording fix. No runtime code changes.

**Tech Stack:** Python `re` + `pathlib` (matching `tests/test_import_boundary.py`'s existing style exactly), pytest, GitHub Actions, GitHub PR templates (plain markdown, no new tooling).

---

### Task 1: Write the static Shift-A boundary test

**Files:**
- Create: `tests/test_a222_shift_a_static_boundary.py`
- Read first (for the exact pattern to mirror): `tests/test_import_boundary.py`

- [ ] **Step 1: Read the existing pattern**

```bash
cat tests/test_import_boundary.py
```

Note its shape: a `ROOT` constant, a list of files/dirs to scan, a list of forbidden regexes, a helper that walks the files and greps each line, and one test function per forbidden-pattern-class that asserts the violations list is empty (with the actual violating lines included in the assertion message for debuggability).

- [ ] **Step 2: Write the failing test first**

```python
"""A222: static, no-live-run-required enforcement of Shift A (agents/arc4/perceive.py,
plan_vetter.py, evaluator.py must never invoke an LLM). Mirrors
tests/test_import_boundary.py's pattern exactly -- same reasoning: catch a boundary
violation with a fast grep-based check instead of requiring a live smoke trace
(scripts/check_compliance_violations.py) to exist before the violation is visible.

A221 Finding 4: no live trace existed to mechanically confirm A218/A219 were
Shift-A-clean; this test closes that gap going forward, at PR time, without
needing a `make smoke` run at all."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The three phases Shift A names as strictly deterministic (ARCHITECTURE.md
# "Graph-Engineering Principles" section). annatar_signals.py is deliberately
# NOT included here -- Annatar is a core-agent reasoning layer (Shift B),
# analogous to resolve/plan, with its own already-shipped, intentional bounded
# LLM escalation tier (resolve_llm_vote). goal_resolver.py and plan_generator.py
# are also deliberately excluded -- Shift A explicitly permits them to invoke
# an LLM behind a deterministic escalation gate.
DETERMINISTIC_PHASE_FILES = [
    ROOT / "agents" / "arc4" / "perceive.py",
    ROOT / "agents" / "arc4" / "plan_vetter.py",
    ROOT / "agents" / "arc4" / "evaluator.py",
]

# The one shape an LLM call takes in this codebase (agents/arc4/ports.py's
# LLMPort protocol: a `.chat(...)` method, threaded through as an `llm_port`
# parameter). Any of these three appearing in a deterministic-phase file means
# something bypassed the phase boundary.
FORBIDDEN_REGEXES = [
    r"\bLLMPort\b",
    r"\bllm_port\b",
    r"\.chat\(",
]


def _find_violations() -> list[str]:
    violations: list[str] = []
    for path in DETERMINISTIC_PHASE_FILES:
        if not path.exists():
            continue
        text = path.read_text()
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern in FORBIDDEN_REGEXES:
                if re.search(pattern, line):
                    violations.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    return violations


def test_deterministic_phases_never_reference_llm_port():
    violations = _find_violations()
    assert violations == [], (
        "Shift A violation: agents/arc4/perceive.py, plan_vetter.py, and "
        "evaluator.py must never reference LLMPort/llm_port/.chat( -- these "
        "phases must be strictly deterministic (ARCHITECTURE.md Graph-Engineering "
        f"Principles). Found:\n" + "\n".join(violations)
    )
```

- [ ] **Step 3: Run it, confirm it passes against the current clean codebase**

```bash
.venv/bin/python -m pytest tests/test_a222_shift_a_static_boundary.py -v
```

Expected: `1 passed`.

- [ ] **Step 4: Prove the test actually catches a violation (TDD discipline — don't skip this)**

Temporarily add a throwaway line to `agents/arc4/evaluator.py` (anywhere, e.g. near the top): `llm_port = None  # temporary — proving A222's test catches this`. Re-run:

```bash
.venv/bin/python -m pytest tests/test_a222_shift_a_static_boundary.py -v
```

Expected: `1 failed`, with the violating line quoted in the assertion output. Then revert the throwaway line:

```bash
git checkout -- agents/arc4/evaluator.py
```

Re-run once more to confirm it's back to `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add tests/test_a222_shift_a_static_boundary.py
git commit -m "A222: add static Shift-A boundary test (no LLM in perceive/vet/evaluate)"
```

---

### Task 2: Wire the new test into `test-a`

**Files:**
- Modify: `Makefile` (the `test-a` target)
- Modify: `.github/workflows/test.yml` (the `test-a` job's pytest invocation)

- [ ] **Step 1: Add to the Makefile**

In `Makefile`, find:

```makefile
test-a: ## fast pre-commit subset: observability, trace durability, import boundary, cycle policy
	$(PYTHON) -m pytest -q \
	  tests/test_observability.py \
	  tests/test_trace_durability.py \
	  tests/test_import_boundary.py \
	  tests/test_a140_cycle_policy.py
```

Replace with:

```makefile
test-a: ## fast pre-commit subset: observability, trace durability, import boundary, cycle policy, shift-a boundary
	$(PYTHON) -m pytest -q \
	  tests/test_observability.py \
	  tests/test_trace_durability.py \
	  tests/test_import_boundary.py \
	  tests/test_a140_cycle_policy.py \
	  tests/test_a222_shift_a_static_boundary.py
```

- [ ] **Step 2: Add to the GitHub Actions workflow**

In `.github/workflows/test.yml`, find the `test-a` job's `Run test-a subset` step:

```yaml
      - name: Run test-a subset
        run: >
          python -m pytest -q
          tests/test_observability.py
          tests/test_trace_durability.py
          tests/test_import_boundary.py
          tests/test_a140_cycle_policy.py
```

Replace with:

```yaml
      - name: Run test-a subset
        run: >
          python -m pytest -q
          tests/test_observability.py
          tests/test_trace_durability.py
          tests/test_import_boundary.py
          tests/test_a140_cycle_policy.py
          tests/test_a222_shift_a_static_boundary.py
```

- [ ] **Step 3: Run the updated target locally**

```bash
PYTHON=.venv/bin/python make test-a
```

Expected: all pass, count includes the new test.

- [ ] **Step 4: Commit**

```bash
git add Makefile .github/workflows/test.yml
git commit -m "A222: wire Shift-A static boundary test into test-a required check"
```

---

### Task 3: Add the PR template's Graph-Engineering Review section

**Files:**
- Create: `.github/PULL_REQUEST_TEMPLATE.md`

- [ ] **Step 1: Write the template**

```markdown
## Summary

<!-- What changed and why. -->

## Graph-Engineering Review

<!-- Required for any change touching agents/arc4/, arc_runtime/, or
run_single_puzzle.py. Invoke the arc-graph-engineering-review skill and
answer honestly -- see .claude/skills/arc-graph-engineering-review/SKILL.md.
Not applicable to docs-only / backlog-only / CI-config-only PRs; delete this
section for those. -->

- **Shift A** (deterministic phases stay LLM-free): 
- **Shift B** (raw results not narrative; single decision owner): 
- **Shift C** (graph vs. local state, and is any tradeoff stated not silent): 
- **Investigation Loop** (only if this PR is a bug/anomaly fix — did the investigation anchor on an entity, test a hypothesis, log a verdict?): 

## Test plan

- [ ] `make test-a` / full suite green
- [ ] <!-- anything else specific to this change -->
```

- [ ] **Step 2: Verify GitHub picks it up**

```bash
gh pr create --help | grep -i template
```

(No command actually needed to "activate" it — GitHub auto-populates new PR descriptions from `.github/PULL_REQUEST_TEMPLATE.md` once it exists at that path. Confirm the file is at the exact path via:)

```bash
ls -la .github/PULL_REQUEST_TEMPLATE.md
```

- [ ] **Step 3: Commit**

```bash
git add .github/PULL_REQUEST_TEMPLATE.md
git commit -m "A222: add required Graph-Engineering Review section to PR template"
```

---

### Task 4: Tighten `CLAUDE.md`'s skill-invocation timing

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Find the current wording**

The relevant sentence today: "Also invoke it before considering ARC runtime work complete, and when investigating a live-run bug or anomaly."

- [ ] **Step 2: Edit it**

Change that sentence to:

```markdown
Also invoke it **before opening the PR** for any architecture-affecting ARC runtime change (not just before considering the work complete — that wording let a real review get skipped in practice, see `backlog/A222.md`), and when investigating a live-run bug or anomaly. Record the findings in the PR's "Graph-Engineering Review" template section, not just in the backlog card's Outcome — a subagent's own self-reported compliance paragraph is not a substitute for this being independently invoked by whoever opens the PR.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "A222: tighten CLAUDE.md's graph-engineering-review timing to pre-PR, not pre-complete"
```

---

### Task 5: Full verification

- [ ] **Step 1: Run everything**

```bash
PYTHON=.venv/bin/python make test-a
PYTHON=.venv/bin/python make test-all
```

Expected: both green, no regressions.

- [ ] **Step 2: Confirm the PR template renders**

Open a PR for this branch itself and confirm the template auto-populates the description (self-verifying — this PR is the first real test of the mechanism it adds).

## Self-Review Notes

- Every task above has real code, not a placeholder.
- Task 1's TDD step (Step 4) is the one place this plan requires an agent to deliberately break the codebase and revert it — flagged explicitly so it isn't skipped as "unnecessary."
- `annatar_signals.py`'s exclusion from `DETERMINISTIC_PHASE_FILES` is explained inline in the test file's own docstring/comment, not just in this plan, so a future reader of the test doesn't wonder why Annatar's LLM call is allowed.
