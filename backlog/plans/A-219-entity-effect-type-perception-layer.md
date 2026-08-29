# Plan: A219 — Compute Entity-Level Effect Type From Frame-to-Frame Correspondence (Part 2, v1 Slice)

## Card metadata

- ID: A219
- Priority: P2
- Layer: ARC runtime
- Dependencies: None functionally; extracted from `backlog/A216.md` Part 2

## Summary

`perceive.py::_assign_correspondence` (A175) already matches entities frame-to-frame via nearest-centroid, same-color matching, assigning a stable `entity_ref` — but only uses it for ID assignment, discarding the rich before/after comparison it implicitly has access to. Add disappearance detection (not computed today) and a pure `classify_effect_type()` function, surfaced in telemetry only — no scoring/graph wiring in this card.

## Technical approach

### Step 1: Read the current state first

1. `backlog/A216.md` Part 2 in full — the taxonomy discussion, the color-lock limitation, and why recolor/merge/split are explicitly out of scope for v1.
2. `agents/arc4/perceive.py::_assign_correspondence` in full (currently ~lines 68-111) — confirm its exact matching logic (`radius: float = 6.0`, `prev_entity.value != entity.value` continue-guard, greedy `claimed` set) before changing anything.
3. `agents/arc4/rule_extraction.py` in full — this already holds deterministic, zero-I/O classification helpers (`magnitude_class`, `shape_class`, `classify_signature`) with an established docstring/testing style. `classify_effect_type()` likely belongs here, following the same pattern, unless a closer look shows a better fit elsewhere.
4. `agents/arc4/telemetry.py::_step_snapshot` — find where per-entity or per-action detail is currently captured, to decide the right place to add the new field(s).

### Step 2: Add disappearance detection

`_assign_correspondence` currently returns only the *updated current-frame* entities; the `claimed` set (indices into `previous` that matched) is computed internally and discarded. Change the function (or add a thin wrapper/sibling that calls it and also returns this) to additionally expose which `previous` entities were *not* in `claimed` — these are the disappeared entities for this frame. Decide during implementation whether to change `_assign_correspondence`'s return signature directly (check all call sites first — search the whole file and its callers for every place `_assign_correspondence` is invoked, to confirm changing its signature is safe) or add a new function that wraps it without changing the existing one's contract, whichever is less disruptive to existing callers.

Write a test proving disappearance is now detected: a two-frame scenario where a previous-frame entity's color/position has no plausible match in the current frame at all (not just moved — genuinely absent), confirming it shows up in the new disappeared-entities output.

### Step 3: `classify_effect_type()`

Pure function, signature roughly:
```python
class EffectType(StrEnum):
    TRANSLATION = "translation"
    GROWTH = "growth"
    SHRINK = "shrink"
    APPEARANCE = "appearance"
    DISAPPEARANCE = "disappearance"
    UNCHANGED = "unchanged"


def classify_effect_type(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any] | None,
    *,
    translation_threshold: float = 2.0,   # centroid distance beyond which "moved" counts as TRANSLATION, not noise
    size_delta_threshold: int = 1,        # cell_count delta beyond which GROWTH/SHRINK fires
) -> EffectType:
    if previous is None and current is not None:
        return EffectType.APPEARANCE
    if previous is not None and current is None:
        return EffectType.DISAPPEARANCE
    # both present -- same entity_ref, compare attributes
    ...
```
Confirm the exact attribute keys available on a `PerceivedEntity`'s `.attributes` dict (`centroid`, `cell_count`, etc. — read `perceive.py` and `agents/arc4/types.py::PerceivedEntity` to get the real field names, don't guess). `translation_threshold`/`size_delta_threshold` are starting-point values, no empirical basis yet — same honest-gap convention as `AnnatarLimits`' own thresholds (A217 precedent) — note this plainly in a docstring/comment, don't spend time trying to derive "correct" values.

Write unit tests covering all six cases with synthetic before/after attribute dicts, including edge cases: a tiny centroid jitter below `translation_threshold` must classify as `UNCHANGED`, not `TRANSLATION` (noise tolerance matters — a real grid has pixel-level jitter that isn't a real move).

### Step 4: Wire into telemetry, read-only

For each entity present in the current frame's correspondence result (matched or newly appeared) and each disappeared entity from Step 2, compute its `EffectType` and add it to the per-step trace data `telemetry.py::_step_snapshot` already builds. Keep this additive — a new field or small structure, not a restructuring of existing telemetry. Write a test confirming the new field appears correctly in a snapshot built from a synthetic two-frame perception sequence.

### Step 5: Regression-confirm out-of-scope areas are untouched

`git diff --stat` must show zero changes to `agents/arc4/plan_generator.py`, `agents/arc4/annatar_signals.py`, `agents/arc4/annatar_state_machine.py`, and `agents/arc4/graph_queries.py`. Re-run existing `perceive.py`/entity-correspondence tests (search `tests/` for `_assign_correspondence` or `entity_ref` coverage, likely in a test file from A175's own card) and confirm byte-for-byte unchanged behavior for the *existing* (non-disappearance) correspondence logic — do not relax the color-lock matching in this card even though A216 Part 2 flagged it as a real limitation; that's explicitly deferred.

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/perceive.py` | `_assign_correspondence` (or a new sibling) gains disappearance-tracking output |
| `agents/arc4/rule_extraction.py` (or a new sibling module, decide during implementation) | New `EffectType` enum, new `classify_effect_type()` function |
| `agents/arc4/telemetry.py` | `_step_snapshot` gains the new field(s) |
| `tests/test_a219_entity_effect_type.py` (new) | Coverage per Steps 2-4 |

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a219_entity_effect_type.py -v
.venv/bin/python -m pytest tests/ -q
make test-a
make test-all
git diff --stat  # confirm plan_generator.py, annatar_signals.py, annatar_state_machine.py, graph_queries.py are NOT in this list
```

## Assumptions/defaults

- `translation_threshold`/`size_delta_threshold` are unvalidated starting points, matching this repo's established "no empirical basis yet, tune with real data" convention (A217's `complex_domain_deepening_multiplier` is the most recent precedent) — do not spend implementation time trying to derive "correct" values.
- If changing `_assign_correspondence`'s return signature turns out to be riskier than expected (many call sites, or a shape used by something outside `perceive.py` in a way that's hard to extend safely), prefer adding a new wrapper function that calls the existing one and separately computes the disappeared-entity list, over changing the existing function's contract — document whichever choice is made and why.
- Do not implement `RECOLOR`/`MERGE`/`SPLIT`/`GLOBAL` in this card, even if it looks easy once `TRANSLATION`/`APPEARANCE`/`DISAPPEARANCE` exist — they're explicitly deferred (see the card's Scope note) because they need changes to `_assign_correspondence`'s core matching logic (color-lock relaxation, many-to-one/one-to-many matching) that deserve their own focused card and review, not to be bundled in here.
