# Plan: A217 — Domain-Aware Anchor Deepening Patience (Cynefin, v1 Slice)

## Card metadata

- ID: A217
- Priority: P1
- Layer: ARC runtime
- Dependencies: None functionally; extracted from `backlog/A216.md` Part 1 (read it first)

## Summary

Investigation anchors currently escalate out of `DEEPENING` after a flat `max_deepening_cycles_before_llm` (default 3) regardless of whether the evidence for that anchor looks like it's still worth investigating. Add a pure Cynefin-style domain classification (`classify_domain()`) computed from evidence already being fetched (`fetch_entity_neighborhood`), and use it to give genuinely `COMPLEX` (disagreeing evidence) anchors more deepening patience, while leaving `CONVERGED`/`CHAOTIC`/`DISORDER` anchors at today's existing behavior.

## Technical approach

### Step 1: Read the current state first

Read in full, in this order:
1. `backlog/A216.md` — Part 1 (the confirmed root-cause evidence: real trace showing zero deepening across 6 distinct coordinates) and Part 3 (the Cynefin research this design is built on). Do not re-derive this research.
2. `backlog/A217.md` — the full card, which has the exact settled design (the `CynefinDomain`/`classify_domain()` code, the exact wiring points, and what's explicitly out of scope). This plan expands on that card's steps; the card is the source of truth if anything here seems ambiguous.
3. `agents/arc4/annatar_state_machine.py` in full — confirm `CycleSignals` (lines 32-62), `AnnatarLimits` (lines 65-72), and `transition()`'s DEEPENING branch (lines 89-101) match what the card describes before editing.
4. `agents/arc4/annatar_signals.py`'s `compute_cycle_signals` (lines 32-115) in full — confirm the `fetch_entity_neighborhood` call and `live_items` computation (lines 57-69) match what the card describes.
5. `agents/arc4/plan_generator.py::_voi_bonus` (lines 408-429) — read this as the reference pattern for the agree/disagree check `classify_domain()` mirrors (same live-filtering, same `to_color`-based outcome comparison). Do NOT modify this function — it's explicitly out of scope for this card.

### Step 2: Decide where `CynefinDomain`/`classify_domain()` live

`annatar_state_machine.py`'s own module docstring states it must stay zero-I/O (mirrors `cycle_policy.py`'s discipline). `classify_domain()` is itself zero-I/O (pure function over an already-fetched evidence list), so it's a legitimate fit for `annatar_state_machine.py` directly — add it there, alongside the existing `InvestigationState`/`AnnatarDecision` enums and `transition()`/`decision_for_state()` functions, following the same style (frozen `StrEnum`, plain function, no I/O). Export it in `annatar_state_machine.py`'s existing `__all__` list (currently lines 146-155) — add `CynefinDomain` and `classify_domain` to it.

Do not create a new module for this — the existing file already holds the right kind of pure logic and this is a small, cohesive addition to it.

### Step 3: TDD — write the `classify_domain()` tests first

New file `tests/test_a217_domain_aware_anchor_patience.py`. Write and run these FIRST, confirm they fail against no implementation, then implement:

```python
from agents.arc4.annatar_state_machine import CynefinDomain, classify_domain


class TestClassifyDomain:
    def test_no_evidence_is_disorder(self):
        assert classify_domain([]) == CynefinDomain.DISORDER

    def test_all_falsified_is_chaotic(self):
        evidence = [
            {"falsified": True, "to_color": 5, "confidence": 0.0},
            {"falsified": True, "to_color": 3, "confidence": 0.0},
        ]
        assert classify_domain(evidence) == CynefinDomain.CHAOTIC

    def test_live_evidence_agreeing_is_converged(self):
        evidence = [
            {"falsified": False, "to_color": 5, "confidence": 0.6},
            {"falsified": False, "to_color": 5, "confidence": 0.4},
            {"falsified": True, "to_color": 2, "confidence": 0.0},  # falsified, ignored
        ]
        assert classify_domain(evidence) == CynefinDomain.CONVERGED

    def test_live_evidence_disagreeing_is_complex(self):
        evidence = [
            {"falsified": False, "to_color": 5, "confidence": 0.5},
            {"falsified": False, "to_color": 3, "confidence": 0.5},
        ]
        assert classify_domain(evidence) == CynefinDomain.COMPLEX

    def test_single_live_item_is_converged_not_complex(self):
        """One live item can't disagree with itself -- degenerate agreement case."""
        assert classify_domain([{"falsified": False, "to_color": 5, "confidence": 0.5}]) == CynefinDomain.CONVERGED

    def test_missing_falsified_key_defaults_to_live(self):
        """Real fetch_entity_neighborhood items may omit the key entirely for a fresh item -- must not crash, must not be treated as falsified."""
        evidence = [{"to_color": 5, "confidence": 0.3}]
        assert classify_domain(evidence) == CynefinDomain.CONVERGED

    def test_missing_to_color_key_does_not_crash(self):
        """A hypothesis-shaped item may not have to_color at all (that's a Rule-specific field) -- must not crash, treat as its own distinct (None) outcome."""
        evidence = [{"falsified": False, "confidence": 0.3}]
        result = classify_domain(evidence)
        assert result in (CynefinDomain.CONVERGED, CynefinDomain.COMPLEX)  # must not raise
```

The last two tests matter: `fetch_entity_neighborhood` returns a mix of `hypotheses` (which may not have `to_color`) and `rules` (which should). Confirm during implementation whether hypothesis items need a different outcome-comparison field than `to_color`, or whether `.get("to_color")` returning `None` uniformly for all hypothesis items (making them all "agree" on `None` as their outcome) is an acceptable degenerate behavior for v1 — if hypotheses and rules get mixed in the same evidence list with different natural outcome fields, document the actual behavior you find/choose in a code comment, don't silently guess.

### Step 4: Implement `classify_domain()` and `CynefinDomain`

In `annatar_state_machine.py`, following the exact shape in `backlog/A217.md`'s "Exact design" section. Run the Step 3 tests, confirm they pass.

### Step 5: Add `domain` to `CycleSignals` and `AnnatarLimits`

`CycleSignals` (currently lines 32-62): add `domain: CynefinDomain = CynefinDomain.DISORDER` as a new field with that default (append near the end of the dataclass, after the existing `veto_reason`/`veto_alternative_action_id` fields added by A212, to minimize diff noise on existing field ordering).

`AnnatarLimits` (currently lines 65-72): add `complex_domain_deepening_multiplier: float = 2.0`.

### Step 6: Wire the DEEPENING escalation check

`transition()`'s DEEPENING branch (currently lines 89-101) — modify exactly as shown in `backlog/A217.md`'s "Wiring into transition()'s DEEPENING escalation" section. Write a test FIRST proving the old flat-constant behavior would fail it:

```python
class TestDomainAwareDeepeningPatience:
    def test_complex_domain_gets_more_deepening_cycles_before_escalation(self):
        """At deepening_cycle_count=3 (the flat default), a CONVERGED-domain
        anchor must escalate to AWAITING_LLM, but a COMPLEX-domain anchor
        with the same cycle count must NOT -- it gets more patience."""
        from agents.arc4.annatar_state_machine import CycleSignals, InvestigationState, transition, CynefinDomain

        base_kwargs = dict(
            meaningful_progress=False, confidence=0.3, untested_remaining=True,
            all_falsified=False, execution_inconclusive=False,
            deepening_cycle_count=3, already_retried=False,
        )
        converged_signals = CycleSignals(**base_kwargs, domain=CynefinDomain.CONVERGED)
        assert transition(InvestigationState.DEEPENING, converged_signals) == InvestigationState.AWAITING_LLM

        complex_signals = CycleSignals(**base_kwargs, domain=CynefinDomain.COMPLEX)
        assert transition(InvestigationState.DEEPENING, complex_signals) == InvestigationState.DEEPENING

    def test_complex_domain_still_escalates_eventually(self):
        """Patience is extended, not infinite -- confirm it still escalates
        once deepening_cycle_count crosses the domain-scaled effective limit
        (default 3 * 2.0 = 6)."""
        from agents.arc4.annatar_state_machine import CycleSignals, InvestigationState, transition, CynefinDomain

        signals = CycleSignals(
            meaningful_progress=False, confidence=0.3, untested_remaining=True,
            all_falsified=False, execution_inconclusive=False,
            deepening_cycle_count=6, already_retried=False, domain=CynefinDomain.COMPLEX,
        )
        assert transition(InvestigationState.DEEPENING, signals) == InvestigationState.AWAITING_LLM

    def test_chaotic_and_disorder_domains_unchanged_from_default(self):
        """Regression: only COMPLEX gets extra patience -- CHAOTIC and
        DISORDER must escalate at exactly the same cycle count as before
        this card (the flat default, 3)."""
        from agents.arc4.annatar_state_machine import CycleSignals, InvestigationState, transition, CynefinDomain

        for domain in (CynefinDomain.CHAOTIC, CynefinDomain.DISORDER):
            signals = CycleSignals(
                meaningful_progress=False, confidence=0.3, untested_remaining=True,
                all_falsified=False, execution_inconclusive=False,
                deepening_cycle_count=3, already_retried=False, domain=domain,
            )
            assert transition(InvestigationState.DEEPENING, signals) == InvestigationState.AWAITING_LLM
```

Confirm these fail against the unmodified `transition()`, then implement the change, confirm they pass.

### Step 7: Wire `compute_cycle_signals` to compute and thread `domain`

`annatar_signals.py`'s `compute_cycle_signals` (lines 32-115) — add the `classify_domain(...)` call inside the existing `if graph_port is not None: ... if anchor_type == "entity" and fetch_neighborhood is not None:` block (lines 57-69), using the full (not pre-filtered) `neighborhood.get("hypotheses", []) + neighborhood.get("rules", [])` list. Thread `domain` through to the final `return CycleSignals(...)` (lines 104-115). Default to `CynefinDomain.DISORDER` when `anchor_type != "entity"`, when `fetch_neighborhood` is unavailable, or when the graph call raises (same `except Exception: degraded = True` path already there — set `domain = CynefinDomain.DISORDER` in that except block too, don't leave it referencing an unset variable).

Write a test confirming `compute_cycle_signals` correctly computes `domain` from a stubbed `graph_port.fetch_entity_neighborhood` returning disagreeing evidence, and defaults to `DISORDER` when the graph port raises — mirror the existing test patterns in `tests/test_a202_annatar_orchestrator_integration.py`'s `TestComputeCycleSignals` class (read it first for the exact stubbing style used elsewhere in this codebase).

### Step 8: Regression-confirm out-of-scope areas are untouched

Run the full test suite and specifically re-run:
- Anything covering `annatar_unproductive_anchor_streak` / `DEFAULT_MAX_UNPRODUCTIVE_ANCHORS` (search `tests/` for these names) — must be byte-for-byte unaffected.
- `tests/test_a178_voi_bonus*.py` or wherever `plan_generator.py::_voi_bonus` is tested (search for it) — must be byte-for-byte unaffected; confirm you did not touch `plan_generator.py` at all in this card.

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/annatar_state_machine.py` | New `CynefinDomain` enum, new `classify_domain()` function, `CycleSignals` gains `domain` field, `AnnatarLimits` gains `complex_domain_deepening_multiplier`, `transition()`'s DEEPENING branch reads domain-scaled limit, `__all__` updated |
| `agents/arc4/annatar_signals.py` | `compute_cycle_signals` computes and threads `domain` |
| `tests/test_a217_domain_aware_anchor_patience.py` (new) | Coverage per Steps 3 and 6-7 |

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a217_domain_aware_anchor_patience.py -v
.venv/bin/python -m pytest tests/ -q
make test-a
make test-all
```

## Live-smoke validation (the real test, per the operator's explicit request)

After unit tests pass and this lands, run a fresh live smoke test (same pattern as tonight's earlier runs: `CAMPY_MCP_CMD="../hippocampy/.venv/bin/python -m campy.adapters.mcp_server" PYTHONPATH=. .venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 20`) and compare against the exact baseline pattern documented in `backlog/A216.md` Part 1 (6 distinct coordinates, each tried exactly once, zero deepening, terminate at step 6). Report in `backlog/A217.md`'s Outcome section: did any anchor this run show `deepening_cycle_count > 0` (i.e. the same entity investigated more than once)? This is the actual signal this card exists to produce — a passing unit-test suite alone does not confirm the fix does anything live, given `COMPLEX` domain classification depends on real graph evidence disagreement actually occurring, which may or may not happen on any single run.

## Assumptions/defaults

- `complex_domain_deepening_multiplier: float = 2.0` is a starting-point value with no empirical basis yet, matching this repo's own established convention for new thresholds (see `AnnatarLimits`'s own docstring: "Starting-point thresholds, no empirical basis yet... tune with real data once this lands, don't treat these as final"). Do not spend implementation time trying to derive a "correct" value — 2.0 is fine for v1, and the Outcome section should note it's unvalidated by real data.
- If `fetch_entity_neighborhood`'s hypothesis items and rule items turn out to need genuinely different outcome-comparison fields (not both usable via `.get("to_color")`), implement the more correct per-item-type comparison rather than forcing a single field name — but document the actual shapes found in a code comment, since the card's sketch assumed a uniform `to_color` field based on `_voi_bonus`'s existing pattern, which reads from `rules` specifically, not the mixed `hypotheses + rules` list this card also includes.
