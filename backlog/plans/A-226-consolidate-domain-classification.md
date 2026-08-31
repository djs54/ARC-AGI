# A226 — Consolidate Domain-Classification Duplication: Plan

## Card metadata

- Card: `backlog/A226.md`
- Depends on: A224 Task 5 (`classify_entity_domain`, the existing shared helper this plan expands), A217/A218 (the original inline logic in `compute_cycle_signals`), A220/A224 Task 2 (the inline logic in `plan_generator.py`)

## Summary

Three call sites independently implement the same `fetch_entity_neighborhood` → `classify_domain()` → (if `DISORDER`) `fetch_entity_history` → upgrade-to-`CHAOTIC` pattern:

1. `agents/arc4/annatar_signals.py::classify_entity_domain` (lines 34-75) — domain-only, already the intended shared helper.
2. `agents/arc4/annatar_signals.py::compute_cycle_signals` (lines 122-174) — inline, also derives `confidence` and a `degraded` flag.
3. `agents/arc4/plan_generator.py::_build_candidates` (lines 301-369) — inline, also runs A208's hard-exclusion (`continue` the candidate) and score-boost logic in between the two fetches.

This plan adds a richer return type so `compute_cycle_signals` and `plan_generator.py` can both consume the consolidated logic without losing their own extra derived values, and without adding a second graph round-trip per entity.

## Implementation approach

### Files

- Modify: `agents/arc4/annatar_signals.py` — add `EntityNeighborhoodClassification`, add `classify_entity_domain_detailed`, rewrite `classify_entity_domain` as a thin wrapper, rewrite `compute_cycle_signals`'s inline block to call the new function.
- Modify: `agents/arc4/plan_generator.py` — rewrite `_build_candidates`'s inline block to call the new function.
- Test: extend `tests/test_a224_workflow_readiness_integration.py`'s `TestClassifyEntityDomain`/`TestClassifyAllEntityDomains` classes with `classify_entity_domain_detailed` coverage (new test class in the same file — it already owns this area). No existing test file should need its assertions changed, only (if anything) its imports/mocks, since behavior is unchanged.

### Step 1: add the shared detailed classifier

In `agents/arc4/annatar_signals.py`, right before `classify_entity_domain`:

```python
from dataclasses import dataclass, field


@dataclass(slots=True)
class EntityNeighborhoodClassification:
    """A226: the full result of classifying one entity's graph evidence --
    classify_entity_domain (below) exposes just `.domain` for callers that
    only need the Cynefin classification; compute_cycle_signals and
    plan_generator.py's _build_candidates need the raw live hypotheses/rules
    too (for confidence scoring and A208's hard-exclusion respectively), and
    previously each re-implemented the whole fetch+classify sequence just to
    get at them. One fetch_entity_neighborhood + conditional one
    fetch_entity_history per entity, same as every one of the three
    pre-consolidation copies -- no new query."""
    domain: CynefinDomain
    live_hypotheses: list[dict[str, Any]] = field(default_factory=list)
    live_rules: list[dict[str, Any]] = field(default_factory=list)
    had_any_record: bool = False
    degraded: bool = False


def classify_entity_domain_detailed(
    entity_ref: Any, graph_port: GraphQueryPort | None
) -> EntityNeighborhoodClassification:
    """A226: the consolidated implementation -- classify_entity_domain,
    compute_cycle_signals, and plan_generator.py's _build_candidates all
    delegate here instead of each independently re-fetching and
    re-classifying. See EntityNeighborhoodClassification's own docstring for
    why the richer return shape exists."""
    domain = CynefinDomain.DISORDER
    live_hypotheses: list[dict[str, Any]] = []
    live_rules: list[dict[str, Any]] = []
    had_any_record = False
    degraded = False

    if graph_port is None:
        return EntityNeighborhoodClassification(domain=domain)

    fetch_neighborhood = getattr(graph_port, "fetch_entity_neighborhood", None)
    if fetch_neighborhood is not None:
        try:
            neighborhood = fetch_neighborhood(entity_ref)
            hypotheses = neighborhood.get("hypotheses", [])
            rules = neighborhood.get("rules", [])
            had_any_record = bool(hypotheses) or bool(rules)
            live_hypotheses = [h for h in hypotheses if not h.get("falsified")]
            live_rules = [r for r in rules if not r.get("falsified")]
            domain = classify_domain(hypotheses + rules)
        except Exception:
            degraded = True
            domain = CynefinDomain.DISORDER

    if domain == CynefinDomain.DISORDER:
        fetch_history = getattr(graph_port, "fetch_entity_history", None)
        if fetch_history is not None:
            try:
                history = fetch_history(entity_ref)
                transitions = history.get("transitions", []) if isinstance(history, Mapping) else []
                changed_count_total = history.get("changed_count_total", 0) if isinstance(history, Mapping) else 0
                if len(transitions) >= 2 and not changed_count_total:
                    domain = CynefinDomain.CHAOTIC
            except Exception:
                degraded = True

    return EntityNeighborhoodClassification(
        domain=domain,
        live_hypotheses=live_hypotheses,
        live_rules=live_rules,
        had_any_record=had_any_record,
        degraded=degraded,
    )
```

### Step 2: `classify_entity_domain` becomes a thin wrapper

Replace the body of the existing `classify_entity_domain` (keep its signature and docstring, update the docstring's "not retrofitted" note since this card is exactly that retrofit):

```python
def classify_entity_domain(entity_ref: Any, graph_port: GraphQueryPort | None) -> CynefinDomain:
    """A224 (consolidated in A226): single-entity Cynefin classification.
    Thin wrapper over classify_entity_domain_detailed -- kept for callers
    that only need the domain value (classify_all_entity_domains, the
    readiness gate). Degrades to DISORDER on a missing graph_port, an
    entity_ref with no real evidence, or any graph-client exception -- same
    conservative default as every other Cynefin read in this codebase."""
    return classify_entity_domain_detailed(entity_ref, graph_port).domain
```

Run `tests/test_a224_workflow_readiness_integration.py::TestClassifyEntityDomain` and `::TestClassifyAllEntityDomains` now — must still pass unchanged (behavior-preserving refactor).

### Step 3: `compute_cycle_signals` uses the consolidated function

Replace lines 122-174 of `compute_cycle_signals` (everything from `domain = CynefinDomain.DISORDER` through the end of the `if domain == CynefinDomain.DISORDER:` history block, but keep the `fetch_untested_actions` block below it untouched) with:

```python
    domain = CynefinDomain.DISORDER
    if graph_port is not None and anchor_type == "entity":
        classification = classify_entity_domain_detailed(anchor_ref, graph_port)
        domain = classification.domain
        confidence = max(
            (h.get("confidence", 0.0) for h in classification.live_hypotheses + classification.live_rules),
            default=0.0,
        )
        if classification.degraded:
            degraded = True
```

Note: `confidence` and `degraded` are already declared above this block in the existing function (`confidence = 0.0`, `degraded = False`) — this just reassigns them instead of the old inline fetch logic doing so. Double-check the exact surrounding variable names against the live file before editing; the plan above assumes the shapes read during this plan's own investigation (2026-08-31) are still current.

### Step 4: `plan_generator.py` uses the consolidated function

Add the import:

```python
from .annatar_signals import classify_entity_domain_detailed
```

Replace lines 301-369's block (from `cynefin_domain: CynefinDomain = CynefinDomain.DISORDER` through the `except Exception: pass`) with:

```python
                cynefin_domain: CynefinDomain = CynefinDomain.DISORDER
                entity_ref = target_info.get("entity_ref")
                if entity_ref is not None and graph_port is not None:
                    try:
                        classification = classify_entity_domain_detailed(entity_ref, graph_port)
                        cynefin_domain = classification.domain

                        # A208: hard-exclusion when the graph has tested this
                        # entity and found nothing that holds -- unchanged
                        # from the pre-consolidation logic, just reading off
                        # the shared classification's had_any_record/
                        # live_hypotheses/live_rules instead of its own copy.
                        nothing_live_remains = not classification.live_hypotheses and not classification.live_rules
                        if classification.had_any_record and nothing_live_remains:
                            continue

                        if classification.live_hypotheses:
                            score += max(h.get("confidence", 0.0) for h in classification.live_hypotheses) * self._limits.entity_neighborhood_weight
                            entity_neighborhood_grounded = True
                        if classification.live_rules:
                            score += max(r.get("confidence", 0.0) for r in classification.live_rules) * self._limits.entity_rule_weight
                            entity_neighborhood_grounded = True

                        if cynefin_domain == CynefinDomain.COMPLEX:
                            score += self._limits.cynefin_complex_bonus
                        elif cynefin_domain == CynefinDomain.CHAOTIC:
                            score -= self._limits.cynefin_chaotic_penalty
                    except Exception:
                        pass
```

Important: the original code's `continue` was inside a `try` block and skipped straight to the next loop iteration on hard-exclusion -- confirm this refactor preserves that exact control flow (the `continue` must still exit the `try` cleanly, which it does in Python). Re-read the full original `try`/`except` structure in the live file before editing; this plan's snippet assumes the boundaries observed during this plan's own investigation are unchanged.

### Step 5: run targeted regression tests

```bash
.venv/bin/python -m pytest tests/test_a224_workflow_readiness_integration.py tests/test_a217_domain_aware_anchor_patience.py tests/test_a218_no_op_rule_signal_for_classify_domain.py tests/test_a220_plan_generator_domain_visibility.py tests/test_a224_cynefin_domain_scoring.py tests/test_a208_entity_neighborhood_hard_exclusion.py tests/test_a224_readiness_probe_selection.py -v
```

Every assertion in every one of these files must pass with zero changes to the assertions themselves (only import/mock adjustments allowed if a test directly patches one of the now-removed inline blocks). If any assertion needs to change, stop and treat that as a real behavior difference this refactor introduced -- investigate before "fixing" the test to match.

### Step 6: full suite + make test-a

```bash
make test-a
make test-all
```

### Step 7: live-smoke + direct graph re-verification

Same method as A225's own verification -- run a live smoke, then query `get_entity_history`/`fetch_entity_neighborhood` directly and call `classify_entity_domain` (still domain-only, now backed by the consolidated function) against a few real entity_refs, confirming the same domain values as a pre-refactor run would have produced. Also spot-check that a plan_generator candidate for a CHAOTIC entity still shows the penalty applied and a COMPLEX one still shows the bonus, and that a fully-falsified entity is still absent from the candidate list (A208 exclusion still fires).

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a224_workflow_readiness_integration.py tests/test_a217_domain_aware_anchor_patience.py tests/test_a218_no_op_rule_signal_for_classify_domain.py tests/test_a220_plan_generator_domain_visibility.py tests/test_a224_cynefin_domain_scoring.py tests/test_a208_entity_neighborhood_hard_exclusion.py tests/test_a224_readiness_probe_selection.py -v
make test-a
make test-all
```

## Assumptions/defaults

- No behavior change anywhere -- this is a pure refactor. If any existing test's assertion needs editing, that's a signal to stop and investigate, not to proceed.
- `EntityNeighborhoodClassification` lives in `annatar_signals.py`, not `annatar_state_machine.py` -- it's derived from live graph I/O (via `classify_entity_domain_detailed`'s fetches), and `annatar_state_machine.py` must stay zero-I/O per A200's original acceptance criteria (still true today, confirmed by this plan's own investigation).
