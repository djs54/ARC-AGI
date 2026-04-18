# A-023 - Force Systematic Untested-Action Probing During Exploration

## Card metadata

- Card: A023
- Priority: P0
- Layer: ARC runtime
- Depends on: A010, A015, A017, A018

## Summary

Insert a proactive exploration guard in `_enforce_action_policy` that forces the agent to try every available action at least once before the reactive ranking/plateau/replan machinery takes over. This unblocks every saturation-dependent card (A010, A015, A017 `rebuild_route_from_saturation`, A018 plateau escalation) on puzzles where the LLM's default ranking starves low-ranked action ids.

## Implementation approach

### 1. Add the counter

In `agents/arc3/orchestrator.py`, locate the `__init__` where `_consecutive_no_progress_steps` is initialized (grep `_consecutive_no_progress_steps` — the assignment lives in the orchestrator class constructor). Add alongside it:

```python
# A023: count of times the untested-probe guard fired in this run,
# for test assertions and operator visibility.
self._untested_probes_forced_in_run: int = 0
```

Locate the place(s) where `_consecutive_no_progress_steps` is reset to 0 (typically after a reward tick). Add the same reset for `self._untested_probes_forced_in_run = 0` only when a full puzzle is finalized (i.e., reset-per-puzzle, not reset-per-step). If the orchestrator has a `_reset_run_state()` or equivalent helper, add the reset there; otherwise reset it alongside the existing per-puzzle fields.

### 2. Insert the proactive guard

In `agents/arc3/orchestrator.py`, method `_enforce_action_policy` at line 4575. The current structure is:

```python
def _enforce_action_policy(self, action, available_actions, ...) -> ARC3Action:
    hyp_ctx = self._hypothesis_context or {}
    coverage = hyp_ctx.get("action_coverage") or {}
    unexplored = [
        candidate for candidate in coverage.get("untested_actions", [])
        if candidate in available_actions
    ]
    # ... observed_effects setup ...
    action_id = action.get("action_id")
    # ... etc ...

    # B209: Route->Execute adherence contract  <-- line ~4605
```

Insert the new guard between the `unexplored` list build (line 4585-4588) and the B209 adherence contract (line 4605). Concretely, add after the existing setup block (after line 4602 `skip_chunk_enforcement = False`):

```python
# A023: proactive untested-action probe.
#
# Fires when the agent has not made progress for two or more consecutive
# steps AND at least one available action has never been tried. Takes
# precedence over the LLM's ranked pick, the B209 route-execute contract,
# and plateau-mode selection — but yields to autopilot and plateau_override
# decision sources which carry higher authority.
if (
    unexplored
    and self._consecutive_no_progress_steps >= 2
    and source not in ("autopilot", "plateau_override")
):
    # Does the active chunk already call for an untested action next?
    chunk_next = None
    if active_chunk and active_chunk.get("estimated_actions"):
        chunk_next = active_chunk["estimated_actions"][0]
    chunk_already_probing = chunk_next in unexplored

    if not chunk_already_probing:
        forced = sorted(unexplored)[0]  # deterministic pick for reproducibility
        self._untested_probes_forced_in_run += 1
        self._emit_trace_event(
            "operation",
            "guard_untested_probe",
            {
                "original_action": action_id,
                "forced_action": forced,
                "untested_available": sorted(unexplored),
                "no_progress_steps": self._consecutive_no_progress_steps,
            },
            {
                "reason": "A023 proactive coverage probe",
                "probes_this_run": self._untested_probes_forced_in_run,
            },
        )
        action.update({
            "action_id": forced,
            "rationale": (
                f"A023 proactive coverage probe: {forced} has not been "
                f"tried yet. Original rationale: {rationale}"
            ),
            "decision_source": "policy_untested_probe",
            "original_action": action_id,
            "adherence_ok": False,
        })
        return action
```

This returns early, before the B209 adherence enforcement at line 4605 and before the chunk-skip guard at line 4698. The ordering matters: probing an untested action is more important than sticking to the route's expected action, because without coverage the route is working off stale/partial evidence.

### 3. Emit coverage snapshot each step

Locate the PERCEIVE phase handler (grep `phase_start.*perceive` or the orchestrator method that fires on per-step PERCEIVE entry). At the end of that handler — or, if PERCEIVE has no clean exit hook, at the start of the MODEL phase — emit a new trace event once per step:

```python
coverage = (self._hypothesis_context or {}).get("action_coverage") or {}
tested = sorted(
    a for a in getattr(self, "_available_actions", [])
    if a not in (coverage.get("untested_actions") or [])
)
untested = sorted(coverage.get("untested_actions") or [])
self._emit_trace_event(
    "operation",
    "exploration_coverage_snapshot",
    {"step": step},
    {
        "tested": tested,
        "untested": untested,
        "initial_exploration_complete": bool(coverage.get("initial_exploration_complete")),
    },
)
```

If there is a cleaner single-step emission point (e.g., the existing `operation: "draft_plan_steps"` at `orchestrator.py:2115-2116`), emit it adjacent to that instead — we want exactly one per step.

### 4. Tests

Create `tests/test_exploration_probing.py`:

```python
import pytest
from agents.arc3.orchestrator import ARCOrchestrator


class _StubBrain:
    async def notify_turn(self, **kwargs): return {}


def _make_orchestrator(monkeypatch):
    """Minimal orchestrator with just the attributes the guard touches."""
    orch = ARCOrchestrator.__new__(ARCOrchestrator)
    orch._hypothesis_context = {}
    orch._solve_context = {}
    orch._consecutive_no_progress_steps = 0
    orch._untested_probes_forced_in_run = 0
    orch._blocked_actions = set()
    orch._available_actions = []
    orch._action_frame_hashes = {}
    orch._action_fatigue = {}
    orch.observed_action_effects = {}
    orch._emit_trace_event = lambda *a, **kw: None
    return orch


def _action(action_id, source="llm", rationale="pick this"):
    return {
        "action_id": action_id,
        "rationale": rationale,
        "decision_source": source,
    }


def test_guard_fires_on_untested_after_two_no_progress(monkeypatch):
    orch = _make_orchestrator(monkeypatch)
    orch._hypothesis_context = {
        "action_coverage": {"untested_actions": ["ACTION4", "ACTION6"]},
    }
    orch._consecutive_no_progress_steps = 2
    result = orch._enforce_action_policy(
        _action("ACTION1"),
        available_actions=["ACTION1", "ACTION2", "ACTION4", "ACTION6"],
    )
    assert result["action_id"] == "ACTION4"  # alphabetical first
    assert result["decision_source"] == "policy_untested_probe"
    assert orch._untested_probes_forced_in_run == 1


def test_guard_silent_when_all_tried(monkeypatch):
    orch = _make_orchestrator(monkeypatch)
    orch._hypothesis_context = {"action_coverage": {"untested_actions": []}}
    orch._consecutive_no_progress_steps = 5
    result = orch._enforce_action_policy(
        _action("ACTION1"),
        available_actions=["ACTION1", "ACTION2"],
    )
    assert result["decision_source"] != "policy_untested_probe"
    assert result["action_id"] == "ACTION1"
    assert orch._untested_probes_forced_in_run == 0


def test_guard_silent_on_fresh_progress(monkeypatch):
    orch = _make_orchestrator(monkeypatch)
    orch._hypothesis_context = {
        "action_coverage": {"untested_actions": ["ACTION4"]},
    }
    orch._consecutive_no_progress_steps = 0
    result = orch._enforce_action_policy(
        _action("ACTION1"),
        available_actions=["ACTION1", "ACTION4"],
    )
    assert result["action_id"] == "ACTION1"
    assert orch._untested_probes_forced_in_run == 0


def test_guard_yields_to_autopilot(monkeypatch):
    orch = _make_orchestrator(monkeypatch)
    orch._hypothesis_context = {
        "action_coverage": {"untested_actions": ["ACTION4", "ACTION6"]},
    }
    orch._consecutive_no_progress_steps = 5
    result = orch._enforce_action_policy(
        _action("ACTION1", source="autopilot"),
        available_actions=["ACTION1", "ACTION4", "ACTION6"],
    )
    assert result["action_id"] == "ACTION1"
    assert orch._untested_probes_forced_in_run == 0
```

If `ARCOrchestrator.__new__` fails because the class has hard dependencies in `__init__`, replace the stub construction with whatever minimal fixture already exists in the `tests/` directory (grep `ARCOrchestrator.__new__` or `make_orchestrator` in existing tests to find the canonical helper). The four assertions above are the invariants — adapt the setup to the project's test style.

### 5. Documentation

In `ARCHITECTURE.md`, under the existing Hypothesis/exploration section, add:

```
#### Exploration-coverage policy (A023)

The orchestrator enforces a proactive exploration guard before the LLM's
ranking and the B209 route-execute contract run. When two consecutive steps
have produced no reward AND at least one action in `available_actions` has
never been tried, the next action is forced to the alphabetically-first
untested candidate. The guard yields to `autopilot` and `plateau_override`
decision sources, and it does not fire when the active chunk already calls
for an untested action next. It emits `guard_untested_probe` and
`exploration_coverage_snapshot` trace events for auditability.
```

## Concrete file additions/edits

- edit `agents/arc3/orchestrator.py`:
  - add `_untested_probes_forced_in_run: int = 0` in `__init__`
  - reset it in the per-puzzle reset helper (if any)
  - insert the new guard block at the top of `_enforce_action_policy` (between line 4602 and line 4605)
  - emit `exploration_coverage_snapshot` once per step near the existing `draft_plan_steps` emission at line 2115-2116
- add `tests/test_exploration_probing.py` with the four tests above
- edit `ARCHITECTURE.md` — add the `#### Exploration-coverage policy (A023)` subsection

## API/interface changes

- New `decision_source` value: `"policy_untested_probe"`. Any code that whitelists decision sources (e.g., `explicit_override_sources` at `orchestrator.py:4606-4614`) should be reviewed for whether to include this new value. Recommendation: **do** include it in the whitelist, because it is an explicit policy override that should bypass downstream B209 adherence enforcement. Add it to that set in the same edit.
- New trace event types: `guard_untested_probe`, `exploration_coverage_snapshot`. Neither is consumed by production code today; the trace writer accepts arbitrary event names.
- New public-ish attribute: `ARCOrchestrator._untested_probes_forced_in_run: int` — test-visible counter; treat as read-only externally.
- No MCP seam changes, no config changes.

## Tests to add or run

- `pytest -q tests/test_exploration_probing.py`
- Regression: `pytest -q tests/test_enforce_action_policy.py` (if present — grep the tests directory)
- Regression: `pytest -q -k "policy or exploration or coverage"`

## Validation commands

- `pytest -q tests/test_exploration_probing.py`
- Smoke verification on the same puzzle the Apr 18 16:59 run used:
  1. Re-run `python run_single_puzzle.py ...` with the same config.
  2. Parse `agent_execution_trace.json`:
     ```sh
     jq '[.[] | select(.event_type=="operation" and .operation=="guard_untested_probe")] | length' agent_execution_trace.json
     ```
     must be ≥ 2 (one probe each for `ACTION4` and `ACTION6`).
  3. Confirm the action trajectory includes every available action at least once within the first 8 steps:
     ```sh
     jq -c 'select(.snapshot_type != "phase_transition") | {step, action_id}' \
        submission_results_single.live.jsonl \
        | jq -s '[.[].action_id] | unique'
     ```
  4. Confirm `initial_exploration_complete` becomes True in at least one `exploration_coverage_snapshot` event:
     ```sh
     jq '.[] | select(.operation=="exploration_coverage_snapshot" and .result.initial_exploration_complete==true) | .details.step' agent_execution_trace.json
     ```

## Assumptions/defaults

- `action_coverage.untested_actions` is the canonical list of action ids not yet attempted in the current puzzle. Verified at `agents/arc3/hypothesis.py:1372-1374` where it is constructed.
- `_consecutive_no_progress_steps` is the canonical no-progress counter. Verified at `orchestrator.py:4616` where it already gates the `relax_adherence` policy.
- Alphabetical order is a deterministic, neutral tiebreaker. If the action ids are not zero-padded (e.g., `ACTION2` vs `ACTION12`), that becomes a concern — but ARC-AGI-3 uses `ACTION1..ACTION6` so the ordering is unambiguous within current scope. If the action space later grows past 9, switch the sort key to `(len(id), id)` in a follow-up.
- The threshold of 2 no-progress steps is intentionally low. Rationale: with 6 available actions, forcing a probe after 2 no-progress steps means coverage completes in at most ~8 steps even if the first two picks were from `observed_effects`. Lower thresholds would starve the LLM's ranking more; higher thresholds would leave coverage incomplete for small budgets. Leaving this as a constant (not a config knob) keeps the failure-mode surface small.
- The guard yields to `autopilot` and `plateau_override`. Rationale: autopilot typically implements geometry-driven navigation for movement puzzles (see `orchestrator.py:4660-4667`), and plateau_override implements A018's plateau policy. Both are higher-authority policies that already have context we don't want to override. `policy_untested_probe` itself **is** added to the explicit-override set so that B209 doesn't re-override it downstream.
- The emission site for `exploration_coverage_snapshot` is "once per step." If PERCEIVE is skipped in some paths (e.g., direct REPLAN→MODEL), emitting from the `draft_plan_steps` site at line 2115-2116 still gives one-per-step because `plan()` is called once per step by the orchestrator driver.
