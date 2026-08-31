# A224 Orchestration Readiness Gate Implementation Plan

> **For agentic workers:** This is the largest, most load-bearing change of the whole A217-A223 investigation. Execute sequentially, one task fully landed (implemented, tested, TDD, independently verified) before the next starts — several tasks touch the same files, and this repo's own established discipline (see A193) treats file-conflict risk as dominant over any parallelism savings for a sequence this interdependent. TDD every task per `superpowers:test-driven-development`. Do not skip the "independently verify" step at the end of each task even though a single agent is executing sequentially — re-read the actual diff before moving on, don't trust your own summary of what you just wrote.

**Goal:** Close the "build the capability, never force anything downstream to depend on it" pattern found five times in A224's own Problem section — a real Cynefin readiness gate before `resolve`, graph-bounded goal escalation, and `cynefin_domain` actually driving `plan_generator.py`'s scoring, all owned by Annatar's existing authority, not a rival gate.

**Architecture:** Five sequential pieces. Pieces 1-2 are small and fully independent of the rest — do them first to reduce risk before the larger pieces 3-5. Pieces 3-5 are interdependent (the gate needs the probe-selection path to have somewhere to route to; both need the `workflow.py` phase-sequence change to actually run).

**Tech Stack:** Same as the rest of `agents/arc4/` — no new dependencies. Reuses `classify_domain()`/`CynefinDomain` (A217), `fetch_entity_neighborhood` (A192/A199, duck-typed access via `getattr(graph_port, "fetch_entity_neighborhood", None)`, same pattern already established in `annatar_signals.py::compute_cycle_signals` — it is NOT in `ports.py`'s formal `GraphQueryPort` Protocol today, a pre-existing gap this plan does not fix).

---

## Task 1: Bound `goal_resolver`'s LLM escalation to graph-confirmed candidates

**Files:**
- Modify: `agents/arc4/goal_resolver.py` (`_merge_llm_patch`, around line 364)
- Test: `tests/test_a224_bounded_goal_escalation.py` (new)

**Problem this closes:** an LLM-proposed `goal_id` that doesn't match any presented, graph-derived hypothesis currently gets appended as a brand-new hypothesis with zero graph evidence (`metadata={"tier": 3, "llm_patch": True}`) — the LLM can escape the graph's bound entirely.

- [ ] **Step 1: Write the failing test**

Read `_merge_llm_patch`'s current full body first (`goal_resolver.py:364-395`ish) to confirm the exact `matched=False` branch before writing the test against it.

```python
def test_unmatched_llm_goal_id_is_not_accepted_as_a_new_ungrounded_hypothesis():
    """A224: an LLM proposing a goal_id that wasn't in the presented,
    graph-derived candidate list must not become a real hypothesis with
    zero graph evidence -- that's the LLM escaping the graph's bound
    entirely, exactly what Shift C says shouldn't happen."""
    resolver = GoalResolver()
    hypotheses = [
        GoalHypothesis(goal_id="blob-3", description="d", confidence=0.3, evidence=("entity:blob:3",)),
    ]
    patch = {"goal_id": "invented-goal-not-in-list", "confidence": 0.9, "reason": "looks promising"}
    updated = resolver._merge_llm_patch(hypotheses, patch)
    goal_ids = {h.goal_id for h in updated}
    assert "invented-goal-not-in-list" not in goal_ids
    assert goal_ids == {"blob-3"}
```

(Exact `GoalResolver`/`GoalHypothesis` constructor args: confirm against `agents/arc4/types.py` and any existing `test_arc4_goal_resolver.py` fixture before writing — this plan gives the shape, not a guaranteed-exact call signature.)

- [ ] **Step 2: Run it, confirm it fails against unmodified code**

```bash
.venv/bin/python -m pytest tests/test_a224_bounded_goal_escalation.py -v
```

Expected: FAIL — unmodified code currently appends the unmatched goal_id.

- [ ] **Step 3: Implement**

In `_merge_llm_patch`, change the `if not matched and goal_id:` branch so it does NOT append a new ungrounded `GoalHypothesis`. Two defensible options, pick one and document the choice in the commit/card Outcome rather than silently deciding:
  (a) Drop the unmatched proposal entirely — the LLM's vote only counts if it picked from what the graph actually offered.
  (b) Keep it, but mark it unambiguously non-authoritative (e.g. `confidence=0.0` forced, or a new `metadata["ungrounded"]=True` flag) so nothing downstream treats it as equivalent to a graph-backed hypothesis.
Read how `updated` is consumed downstream (`plan_generator.py`/wherever `active_goal.selected` gets chosen from this list) before picking — if confidence alone already gates selection, (b) may be simpler and preserve more LLM signal; if not, (a) is safer.

- [ ] **Step 4: Run test, confirm pass. Run full suite, confirm no regressions.**

```bash
.venv/bin/python -m pytest tests/test_a224_bounded_goal_escalation.py -v
.venv/bin/python -m pytest tests/ -q
```

- [ ] **Step 5: Commit**

```bash
git add agents/arc4/goal_resolver.py tests/test_a224_bounded_goal_escalation.py
git commit -m "A224 Task 1: bound goal_resolver's LLM escalation to graph-confirmed candidates"
```

---

## Task 2: Wire `cynefin_domain` into `plan_generator.py`'s scoring

**Files:**
- Modify: `agents/arc4/plan_generator.py` (`_build_candidates`, the block A220 added `metadata["cynefin_domain"]` to)
- Test: `tests/test_a224_cynefin_domain_scoring.py` (new)

**Problem this closes:** A220 surfaced `cynefin_domain` in candidate metadata; nothing reads it for scoring. `_voi_bonus` (the existing agree/disagree scoring term `classify_domain()` itself was modeled on, per A217's own docstring) stays untouched — this is a NEW, separate term, not a rewrite of `_voi_bonus`.

- [ ] **Step 1: Write the failing test** — a `COMPLEX`-domain candidate should score higher than an otherwise-identical `CONVERGED`/`DISORDER` one; a `CHAOTIC`-domain candidate should score lower. Use the existing `TestScoreRegressionUnchanged`-style fixture pattern from `tests/test_a220_plan_generator_domain_visibility.py` as a template for constructing a real entity-neighborhood mock.

- [ ] **Step 2: Confirm it fails against unmodified code.**

- [ ] **Step 3: Implement** — add a small scoring term (name it clearly, e.g. `_domain_bonus`, separate function mirroring `_voi_bonus`'s own shape) applied alongside the existing score computation in `_build_candidates`. `COMPLEX` gets a positive bonus (worth more probing — live evidence disagrees, matching A217's own patience-multiplier reasoning applied to scoring instead of patience). `CHAOTIC` gets a penalty (extending A208's existing hard-exclusion into a *soft* signal for candidates that survive exclusion but are trending dead). `CONVERGED`/`DISORDER` unchanged. Starting-point magnitudes, not empirically tuned — say so in the code comment, matching this codebase's own honest-gap convention for every other new threshold this session (`AnnatarLimits`' own docstring: "Starting-point thresholds, no empirical basis yet").

- [ ] **Step 4: Regression-prove `_voi_bonus` itself is untouched** (same discipline A220 used — `TestVoiBonusUntouched`-style test).

- [ ] **Step 5: Run full suite, commit.**

---

## Task 3: The readiness-gate pure function, in Annatar's module home

**Files:**
- Modify: `agents/arc4/annatar_state_machine.py` (or a clearly-named sibling if `annatar_state_machine.py` is getting too large — judgment call, but default to adding here since this is Annatar's own decision logic, matching this card's explicit "no rival component" constraint)
- Test: `tests/test_a224_readiness_gate.py` (new)

**Problem this closes:** nothing today decides "is the graph ready for `resolve` to commit" — `resolve` just runs unconditionally every cycle.

- [ ] **Step 1: Write the failing tests** for a new pure function, e.g.:

```python
def readiness_status(
    entity_domains: Mapping[Any, CynefinDomain],
    *,
    step_index: int,
    max_cycles: int,
    budget_fraction_before_fallthrough: float = 0.5,
) -> ReadinessStatus:
    """Pure, no I/O -- entity_domains is {entity_ref: CynefinDomain}, already
    computed by the caller (classify_domain() per entity, via already-fetched
    fetch_entity_neighborhood/fetch_entity_history data -- this function does
    not fetch anything itself, mirrors transition()'s own "caller computes
    CycleSignals, this function only decides" discipline)."""
```

Returns something like a small `ReadinessStatus` dataclass/enum: `READY` (no entity is `DISORDER`), `NOT_READY` (at least one is, budget allows continuing), `PARTIAL_FALLTHROUGH` (at least one still `DISORDER`, but `step_index / max_cycles >= budget_fraction_before_fallthrough`).

Tests: all-non-disorder → `READY`. one `DISORDER`, early step → `NOT_READY`. one `DISORDER`, `step_index` past the budget fraction → `PARTIAL_FALLTHROUGH`. empty `entity_domains` (no entities perceived at all — a real possible case, e.g. a blank grid) → decide and test explicitly what this returns (`READY` is the defensible default: nothing to map means nothing blocks proceeding — don't leave this unhandled).

- [ ] **Step 2-4: TDD as usual** (fail, implement, pass, full suite).

- [ ] **Step 5: Commit.**

---

## Task 4: Deterministic probe-selection for the "not ready" path

**Files:**
- Modify: `agents/arc4/plan_generator.py` (or a new sibling module if this doesn't fit `_build_candidates`'s existing shape well — judgment call) — needs a path that, given the current entity list and their domains, picks the next `DISORDER` entity and constructs a probe action for it, WITHOUT going through the normal LLM-escalation/RETRY-vulnerable candidate-scoring flow.
- Test: `tests/test_a224_readiness_probe_selection.py` (new)

**Problem this closes:** the normal `plan_generator`/`RETRY` path is tuned for deepening an already-anchored investigation and would prematurely abandon anchors during broad initial mapping, for the same reasons it does today (see A224's Problem section, point 5).

- [ ] **Step 1: Read `_click_targets`/`_build_candidates`'s existing entity-to-candidate construction first** (needed to reuse the same coordinate/action-payload shape a real probe action needs — don't reinvent candidate construction, adapt it).

- [ ] **Step 2-5: TDD** a function that, given entities with their domains, deterministically picks one `DISORDER` entity (simplest defensible tie-break: first by salience, matching existing `_click_targets` ordering — don't invent a new priority scheme without evidence it's needed) and returns a real, executable candidate/action for it.

- [ ] **Step 6: Commit.**

---

## Task 5: Wire the gate into `workflow.py`'s phase sequence

**Files:**
- Modify: `agents/arc4/workflow.py` (between the existing `perceive` and `resolve` calls, `workflow.py:112-119`)
- Modify: `agents/arc4/types.py` (`WorkflowState` — likely one new field, e.g. `readiness_gate_resolved: bool = False`, to cache the decision once `READY`/`PARTIAL_FALLTHROUGH` is reached so the gate isn't recomputed — and its full `classify_domain()` calls per entity — every remaining cycle of the episode; justify this addition explicitly, don't add speculative fields beyond what's needed)
- Modify: `agents/arc4/telemetry.py` (surface `readiness_gate_partial`/`entities_mapped`/`entities_total` per this card's acceptance criteria — a real, queryable trace fact, not implied)
- Test: `tests/test_a224_workflow_readiness_integration.py` (new) — integration-level, exercising the full perceive→gate→(probe-path OR resolve) branch

- [ ] **Step 1-N: TDD**, but this is the highest-risk task in the sequence (touches the most load-bearing file in the runtime). Before writing the integration change:
  - Confirm exactly how `perception_payload.entities` maps to the `entity_ref` keys `classify_domain()`/`fetch_entity_neighborhood` expect (should already be consistent per A175/A192, but verify against real code, don't assume).
  - Confirm the probe-path's `execute`/`evaluate` calls reuse the SAME `execute`/`evaluate` phase dependencies the normal path uses (this should be a routing decision — which action gets chosen and whether `resolve`/`plan` run at all — not a parallel execution pipeline).

- [ ] **Final step: full suite + `make test-a` + `make test-all` green, then at least one live-smoke run**, per this card's own acceptance criteria — analyze honestly whether the gate reaches `READY` naturally, whether `CHAOTIC` finally fires, whether `PARTIAL_FALLTHROUGH` ever triggers. Update `backlog/A224.md`'s Outcome section with real findings, not a claimed-complete summary.

## Self-Review Notes

- Tasks 1-2 are fully spec'd, TDD-able immediately, no placeholders.
- Tasks 3-5 name the exact problem, the exact function shape, and the exact test cases to cover, but leave some implementation-detail judgment calls explicit rather than presupposed (tie-break rules, exact field names) — flagged inline each time rather than silently guessed, per this session's own "don't presuppose the answer" convention (mirrors A220's own "Next step" section's discipline).
- Task 5 is deliberately the last, highest-risk step — everything else should be independently correct and tested before `workflow.py` itself changes.
