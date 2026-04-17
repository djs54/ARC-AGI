# B-204 — Timeline Visibility: Phase Answer Surfacing and Event Source Differentiation

- **Card:** backlog/B204.md
- **Priority:** P1
- **Dependencies:** B202 complete (provides `_last_response_perception`), B203 recommended first

## Summary

Two targeted changes to make the timeline useful as a diagnostic: (1) the perceive phase_answer shows actual response data instead of a generic string; (2) the master timeline distinguishes SideQuests memory calls from ARC API responses using `source` and `event_detail`.

## Technical Approach

### Change 1: Update `_phase_answer_for` PERCEIVE case

**File:** `agents/arc3/runner.py`, method `_phase_answer_for` (line 1611), PERCEIVE case at line 1618.

```python
# Before:
if phase == SolvePhase.PERCEIVE.value:
    return "Initial observation captured and memory retrieval seeded."

# After:
if phase == SolvePhase.PERCEIVE.value:
    perception = getattr(orchestrator, "_last_response_perception", None)
    if perception and perception.get("step", 0) > 0:
        delta = perception.get("delta", {})
        n_changed = delta.get("n_cells_changed", 0)
        effect = delta.get("apparent_effect")
        direction = delta.get("direction")
        actions = ", ".join(perception.get("available_actions", []))
        delta_str = f"{n_changed} cells changed"
        if effect:
            delta_str += f", {effect}"
        if direction:
            delta_str += f", direction={direction}"
        return (
            f"State={perception.get('state')}, reward={perception.get('reward')}, "
            f"done={perception.get('done')}. Grid: {delta_str}. "
            f"Actions: {actions or 'pending'}."
        )
    return "Initial observation captured and memory retrieval seeded."
```

This reads the `_last_response_perception` dict stored by `perceive_step_response()` (B202). The bootstrap case (step == 0, or perception not set yet) falls through to the original string.

### Change 2: Differentiate event sources in `run_single_puzzle.py`

**File:** `run_single_puzzle.py`

#### 2a. Classify `event_detail` in call_timeline construction (line ~282)

Add a classification helper before the `call_timeline.append()` block:

```python
SIDEQUESTS_CALLS = {
    "notify_turn", "current_truth", "recall_lessons", "recall_plans",
    "analogical_search", "register_plan", "report_outcome",
    "recall_procedures", "get_knowledge_gaps", "branch_quest",
    "upsert_lesson", "explore_graph", "reconstruct_timeline",
}
ARC_API_CALLS = {"arc_api_action", "RESET", "ACTION1", "ACTION2",
                 "ACTION3", "ACTION4", "ACTION5", "ACTION6"}

if call_type in SIDEQUESTS_CALLS:
    event_detail_classified = "SideQuests memory/planning call"
elif call_type in ARC_API_CALLS:
    event_detail_classified = "ARC API interaction"
else:
    event_detail_classified = "internal orchestration"
```

Use `event_detail_classified` instead of the hardcoded string in the `call_timeline.append()` call at line ~288.

#### 2b. Update `source` field in master_timeline export (line ~403)

Currently all call_timeline events get `source: "arc_server"`. Change to:

```python
# Determine source based on event type
event_type = event.get("event")
call_type_for_source = (event.get("data") or {}).get("call_type") or event.get("name", "")

if event_type in ("request", "response"):
    source = "arc_api"
elif call_type_for_source in SIDEQUESTS_CALLS:
    source = "sidequests"
else:
    source = "arc_server"  # fallback for any uncategorized calls

master_timeline.append({
    "source": source,
    ...
})
```

Note: `SIDEQUESTS_CALLS` set defined at module level or imported — same set used in 2a.

## Concrete File Changes

| File | Lines | Change |
|------|-------|--------|
| `agents/arc3/runner.py` | 1618-1619 | Replace 2-line PERCEIVE case in `_phase_answer_for` with 12-line version |
| `run_single_puzzle.py` | ~282-298, ~401-413 | Add call_type classifier; update `event_detail` and `source` in timeline export |

## Test Plan

No new unit tests needed — these are reporting-layer changes. Verified via smoke test.

**Smoke test verification commands:**
```bash
python run_single_puzzle.py

# Verify perceive answer is informative (not generic)
python3 -c "
import json
d = json.load(open('master_timeline.json'))
perceive_transitions = [e for e in d if e.get('name') == 'phase_transition' and 'perceive' in str(e.get('what',''))]
for p in perceive_transitions[:5]:
    print(p.get('phase_answer'))
"

# Verify source differentiation
python3 -c "
import json
from collections import Counter
d = json.load(open('master_timeline.json'))
sources = Counter(e.get('source') for e in d)
print('Sources:', dict(sources))
notify_sources = Counter(e.get('source') for e in d if e.get('name') == 'notify_turn')
print('notify_turn sources:', dict(notify_sources))
"

# Verify event_detail differentiation
python3 -c "
import json
from collections import Counter
d = json.load(open('master_timeline.json'))
details = Counter(e.get('event_detail') for e in d)
print('event_detail breakdown:', dict(details))
"
```

**Expected results:**
- perceive transition phase_answers show e.g. `"State=NOT_FINISHED, reward=0.0, done=False. Grid: 4 cells changed, no_effect. Actions: ACTION1, ACTION2, ACTION3."`
- `sources` shows `{"sidequests": N, "arc_api": M, "agent_trace": K, "live_snapshot": J}`
- `event_detail` shows `{"SideQuests memory/planning call": N, "ARC API interaction": M, ...}`
- Bootstrap perceive still shows `"Initial observation captured and memory retrieval seeded."`

## Acceptance Criteria
See backlog/B204.md.

## Risks

- **B202 dependency:** If `_last_response_perception` is not set (B202 not complete), the PERCEIVE case gracefully falls back to the original string. Safe to implement before B202 if needed.
- **SIDEQUESTS_CALLS completeness:** The set may miss some tool names. Unknown call types fall through to `"arc_server"` source, which is the pre-existing behavior — so missing entries are non-breaking.
