# Plan: A190 — Make `book_id` a First-Class `PlanCandidate` Field

## Card metadata

- ID: A190
- Priority: P2
- Layer: ARC runtime
- Dependencies: A188, A189

## Summary

`PlanCandidate.book_id` currently exists only as an untyped `metadata["book_id"]` string, forcing every one of six sites across `plan_vetter.py`, `workflow.py`, `temporal_workflows.py`, and `plan_generator.py` to independently re-derive it via `metadata.get("book_id") or action_id`. The internal `_CandidateRecord` (the only thing `PlanCandidate` is ever built from in production) already carries a correctly-typed `book_id: str` field — it's discarded at the single conversion point. Add `book_id` as a real field on `PlanCandidate`, resolved once at construction, and repoint every downstream reader to it directly.

## Technical approach

### 1. `agents/arc4/types.py` — add the field

```python
@dataclass(slots=True)
class PlanCandidate:
    action_id: str
    goal_id: str | None = None
    score: float = 0.0
    rationale: str = ""
    expected_effect: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    predicted_outcome: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    book_id: str = ""

    def __post_init__(self) -> None:
        if not self.book_id:
            self.book_id = str(self.metadata.get("book_id") or self.action_id)

    def to_dict(self) -> dict:
        return {
            "action_id": self.action_id,
            "goal_id": self.goal_id,
            "score": self.score,
            "rationale": self.rationale,
            "expected_effect": self.expected_effect,
            "payload": self.payload,
            "predicted_outcome": self.predicted_outcome,
            "metadata": self.metadata,
            "book_id": self.book_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PlanCandidate:
        return cls(
            action_id=d["action_id"],
            goal_id=d.get("goal_id"),
            score=d.get("score", 0.0),
            rationale=d.get("rationale", ""),
            expected_effect=d.get("expected_effect"),
            payload=d.get("payload", {}),
            predicted_outcome=d.get("predicted_outcome", {}),
            metadata=d.get("metadata", {}),
            book_id=d.get("book_id", ""),
        )
```

`book_id` must come *after* `metadata` in field order (Python dataclasses require it — `__post_init__` reads `self.metadata`, which must already be set, and slots dataclasses still run `__post_init__` after all fields are assigned, so field order only matters for the positional-arg constructor signature, not for `__post_init__` correctness — but keep `metadata` before `book_id` anyway for constructor-argument readability).

`from_dict(d.get("book_id", ""))` is the backward-compatibility path: any Temporal workflow state serialized *before* this change has no `"book_id"` key, `d.get(..., "")` returns `""`, `__post_init__` sees falsy `book_id` and falls back to `metadata.get("book_id") or action_id` exactly as today — old in-flight durable state resolves correctly with zero migration needed.

### 2. `agents/arc4/plan_generator.py` — stop discarding the typed value

In `_to_plan_candidate` (~line 761-776), pass `book_id` explicitly instead of letting `__post_init__` re-derive it from metadata:

```python
@staticmethod
def _to_plan_candidate(candidate: _CandidateRecord | None, goal_id: str) -> PlanCandidate | None:
    if candidate is None:
        return None
    metadata = dict(candidate.metadata)
    metadata.setdefault("goal_id", goal_id)
    return PlanCandidate(
        action_id=candidate.action_id,
        goal_id=goal_id,
        score=candidate.score,
        rationale=candidate.rationale,
        expected_effect=candidate.expected_effect,
        payload=dict(candidate.payload),
        predicted_outcome=dict(candidate.predicted_outcome or {}),
        metadata=metadata,
        book_id=candidate.book_id,
    )
```

In `_build_candidates` (~lines 308-323), the veto-alternative `_CandidateRecord` construction currently computes the fallback book_id twice (once for its own `book_id=` field, once for `metadata["book_id"]`, required to stay in sync by hand):

```python
book_id=str((state.latest_veto_alternative.metadata or {}).get("book_id") or veto_action),
...
metadata={
    "book_id": str((state.latest_veto_alternative.metadata or {}).get("book_id") or veto_action),
    ...
},
```

Replace both with a single read of the already-resolved field on the source `PlanCandidate`:

```python
book_id=state.latest_veto_alternative.book_id,
...
metadata={
    "book_id": state.latest_veto_alternative.book_id,
    ...
},
```

(`state.latest_veto_alternative` is itself a `PlanCandidate`, so `.book_id` is already correctly resolved — no `metadata.get`/`or` needed at all.)

### 3. `agents/arc4/plan_vetter.py` — delete the helper, read the field

Delete `_book_id()` entirely (lines 12-17). Update its 3 call sites:

- `vet()`: `candidate_book_id = _book_id(candidate)` -> `candidate_book_id = candidate.book_id`
- `_choose_alternative()`, alternatives loop: `int(state.action_attempt_counts.get(_book_id(alternative), 0))` -> `int(state.action_attempt_counts.get(alternative.book_id, 0))`
- `_choose_alternative()`, `latest_veto_alternative` fallback: same substitution using `state.latest_veto_alternative.book_id`

### 4. `agents/arc4/workflow.py` — read the field, not metadata

```python
@staticmethod
def _record_execution_attempt(state: WorkflowState, execution: ExecutionResult) -> None:
    action_key = execution.candidate.book_id if execution.candidate is not None else execution.action_id
    state.action_attempt_counts[action_key] = state.action_attempt_counts.get(action_key, 0) + 1
```

Apply the same substitution in `_record_evaluation_state`. This is a narrower, more honest fallback than before: `execution.candidate is None` is a genuine "no candidate on this execution" case, not a keying convention being re-derived.

### 5. `agents/arc4/temporal_workflows.py` — read the serialized field

```python
candidate = execution.get("candidate") if isinstance(execution, dict) else None
action_id = execution.get("action_id", "")
action_key = (candidate.get("book_id") if isinstance(candidate, dict) else None) or action_id
```

Since step 1 adds `book_id` to `to_dict()`, any candidate dict produced after this change carries it directly. The `or action_id` fallback covers both "no candidate" and "candidate dict predates this change" (defensive, cheap, matches `PlanCandidate.__post_init__`'s own fallback semantics for consistency).

### 6. Audit pass

`grep -n "action_attempt_counts\|action_falsification_counts\|book_id" agents/arc4/*.py` and inspect every remaining match not covered by steps 1-5. Classify each as: (a) now reads `.book_id` directly, (b) pure pass-through with no key derivation, or (c) a genuine miss requiring the same fix. Record every site examined and its classification in the card's Resolution.

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/types.py` | `PlanCandidate` gains `book_id: str = ""` + `__post_init__`; `to_dict()`/`from_dict()` carry it |
| `agents/arc4/plan_generator.py` | `_to_plan_candidate` passes `book_id` explicitly; `_build_candidates`'s veto-alternative construction reads `.book_id` instead of recomputing |
| `agents/arc4/plan_vetter.py` | `_book_id()` deleted; 3 call sites read `.book_id` directly |
| `agents/arc4/workflow.py` | `_record_execution_attempt`, `_record_evaluation_state` read `execution.candidate.book_id` |
| `agents/arc4/temporal_workflows.py` | Mirror site reads dict's `book_id` key |
| `tests/test_a190_book_id_first_class_field.py` (new) | Coverage (see Tests) |

## Tests

New `tests/test_a190_book_id_first_class_field.py`:

1. `PlanCandidate` constructed with `metadata={"book_id": "ACTION6@1,2"}` and no explicit `book_id` arg resolves `.book_id == "ACTION6@1,2"` via `__post_init__`.
2. `PlanCandidate` constructed with no `book_id` in metadata resolves `.book_id == action_id` (non-click case).
3. `PlanCandidate` constructed with an explicit `book_id=` arg keeps it, ignoring metadata (covers `_to_plan_candidate`'s explicit-pass path).
4. `to_dict()` includes `"book_id"`; `from_dict()` round-trips it exactly.
5. `from_dict()` on a dict with no `"book_id"` key (simulating pre-change serialized Temporal state) still resolves `.book_id` correctly via the `__post_init__` fallback.
6. `plan_vetter.py::vet()` and `_choose_alternative()` behave identically to A188's existing tests (reuse/adapt `tests/test_a188_*.py` scenarios, confirming the field-based read produces the same veto decisions as the old metadata-based helper).
7. `plan_generator.py::_build_candidates`'s veto-alternative candidate has matching `book_id`/`metadata["book_id"]` for both a click-target and non-click veto alternative.

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a190_book_id_first_class_field.py -v
.venv/bin/python -m pytest tests/test_a188_vetter_keys_by_book_id.py tests/test_a189_llm_patch_scoped_to_one_candidate.py -v
make test-a
make test-all
grep -rn "action_attempt_counts\|action_falsification_counts\|book_id" agents/arc4/*.py
```

The final `grep` is the audit checkpoint — every match must be traceable in the Resolution to either "reads `.book_id` directly," "pure pass-through," or a newly-fixed miss.

## Assumptions/defaults

- This is behavior-preserving except that the resolution point moves from N read sites to 1 construction site — no scoring, veto-threshold, or key-resolution outcome should change for any existing test or live scenario. If the audit (step 6) finds a genuine miss beyond the five known sites, fix it and call it out explicitly in the Resolution as a behavior-affecting fix, not folded silently into the refactor.
- `slots=True` on `PlanCandidate` does not prevent `__post_init__` — Python dataclasses support `__post_init__` normally with `slots=True`; only `__slots__`-incompatible patterns (e.g. default mutable class attributes outside `field(default_factory=...)`) are restricted, and this change doesn't introduce any.
- Full `make test-all` run is required (not just targeted tests) because adding a field to a `slots=True` dataclass changes its `__eq__`/`__repr__`/positional-constructor shape — any test asserting `PlanCandidate` equality, snapshot/repr comparison, or positional construction must be checked for incidental breakage even though this card doesn't intend to change observable behavior.
