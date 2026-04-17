# B-203 — Fix Stale tool_rules and phase_owner Mappings: Implementation Plan

- **Card:** backlog/B203.md
- **Priority:** P1
- **Dependencies:** B201 complete

## Summary

A single-function patch in `runner.py` to replace pre-B201 phase name strings with the correct B201 names in `_build_orchestration_report()`. Currently causes 120 false phase violations per run.

## Technical Approach

All changes are in `agents/arc3/runner.py`, method `_build_orchestration_report()` (line ~2094).

### 1. Update `phase_owner` (line ~2095)

```python
# Before:
phase_owner = {
    "bootstrap": "harness",
    "perceive": "orchestrator",
    "plan": "orchestrator",
    "hypothesize": "orchestrator",
    "solve": "orchestrator",
    "act": "LLM",
    "ingest": "orchestrator",
    "evaluate": "harness",
}

# After:
phase_owner = {
    "bootstrap": "harness",
    "perceive": "orchestrator",
    "model": "orchestrator",
    "hypothesize": "orchestrator",
    "route": "orchestrator",
    "execute": "LLM",
    "evaluate": "harness",
    "replan": "harness",
}
```

Changes: `"plan"` → `"model"`, `"solve"` → `"route"`, `"act"` → `"execute"`, removed `"ingest"` (now part of evaluate), added `"replan"`.

### 2. Update `decision_flow` (line ~2105)

```python
# Before:
decision_flow = {
    "bootstrap": {"proposer": "harness", "executor": "harness"},
    "perceive": {"proposer": "orchestrator", "executor": "SideQuests"},
    "plan": {"proposer": "orchestrator", "executor": "SideQuests"},
    "hypothesize": {"proposer": "orchestrator", "executor": "orchestrator"},
    "solve": {"proposer": "orchestrator", "executor": "orchestrator"},
    "act": {"proposer": "LLM", "executor": "orchestrator"},
    "ingest": {"proposer": "orchestrator", "executor": "SideQuests"},
    "evaluate": {"proposer": "harness", "executor": "harness"},
}

# After:
decision_flow = {
    "bootstrap": {"proposer": "harness", "executor": "harness"},
    "perceive": {"proposer": "orchestrator", "executor": "SideQuests"},
    "model": {"proposer": "orchestrator", "executor": "SideQuests"},
    "hypothesize": {"proposer": "orchestrator", "executor": "orchestrator"},
    "route": {"proposer": "orchestrator", "executor": "orchestrator"},
    "execute": {"proposer": "LLM", "executor": "orchestrator"},
    "evaluate": {"proposer": "harness", "executor": "harness"},
    "replan": {"proposer": "harness", "executor": "harness"},
}
```

### 3. Update `tool_rules` allowed_phases (line ~2115)

```python
# Before:
tool_rules = {
    "branch_quest": {"owner": "SideQuests", "allowed_modes": ["write"], "allowed_phases": ["bootstrap"]},
    "notify_turn":  {"owner": "SideQuests", "allowed_modes": ["write"], "allowed_phases": ["bootstrap", "act", "ingest", "evaluate", "finalization"]},
    "current_truth":{"owner": "SideQuests", "allowed_modes": ["read"],  "allowed_phases": ["bootstrap", "act", "ingest", "solve"]},
    "recall_lessons":{"owner": "SideQuests","allowed_modes": ["read"],  "allowed_phases": ["bootstrap", "solve", "ingest"]},
    "register_plan":{"owner": "SideQuests", "allowed_modes": ["write"], "allowed_phases": ["bootstrap", "solve"]},
    "report_outcome":{"owner": "SideQuests","allowed_modes": ["write"], "allowed_phases": ["evaluate", "solve", "finalization"]},
}

# After:
tool_rules = {
    "branch_quest": {"owner": "SideQuests", "allowed_modes": ["write"], "allowed_phases": ["bootstrap"]},
    "notify_turn":  {"owner": "SideQuests", "allowed_modes": ["write"], "allowed_phases": ["bootstrap", "perceive", "execute", "evaluate", "finalization"]},
    "current_truth":{"owner": "SideQuests", "allowed_modes": ["read"],  "allowed_phases": ["bootstrap", "perceive", "execute", "evaluate", "route"]},
    "recall_lessons":{"owner": "SideQuests","allowed_modes": ["read"],  "allowed_phases": ["bootstrap", "perceive", "route", "evaluate"]},
    "register_plan":{"owner": "SideQuests", "allowed_modes": ["write"], "allowed_phases": ["bootstrap", "route"]},
    "report_outcome":{"owner": "SideQuests","allowed_modes": ["write"], "allowed_phases": ["evaluate", "route", "finalization"]},
}
```

Key mappings applied:
- `"act"` → `"execute"`
- `"ingest"` → `"evaluate"`
- `"solve"` → `"route"`
- Added `"perceive"` to tools that will fire during per-step perceive (notify_turn, current_truth, recall_lessons)

## Concrete File Changes

| File | Lines | Change |
|------|-------|--------|
| `agents/arc3/runner.py` | ~2095-2122 | Replace 3 dicts in `_build_orchestration_report()` |

## Validation Commands

```bash
# Run runner tests
pytest tests/test_arc3_durable_runner.py -v

# Smoke test — check violation count
python run_single_puzzle.py
python3 -c "
import json
d = json.load(open('submission_results_single.json'))
r = d[0]
v = r.get('orchestration_report', {}).get('violations', [])
print(f'Violations: {len(v)}')
print(f'Status: {r[\"orchestration_report\"][\"status\"]}')
"
```

## Acceptance Criteria
See backlog/B203.md.

## Risks

- **B202 dependency for `perceive`:** The `perceive` phase is added to allowed_phases above. If B202 is not yet complete, tools won't actually fire in `perceive` mid-step, so adding it is harmless (no violations created for phases that don't run yet). This is safe to implement before or after B202.
- **Minimal change risk:** This is a pure reporting fix — no behavior changes to phase transitions or tool invocations.
