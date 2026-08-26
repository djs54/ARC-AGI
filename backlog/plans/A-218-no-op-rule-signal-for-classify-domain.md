# Plan: A218 — Audit: Revisit A213's No-Op Rule Signal Now That A217 Is a Real Consumer

## Card metadata

- ID: A218
- Priority: P2
- Layer: ARC runtime
- Dependencies: A213, A217

## Summary

A213 concluded a no-op rule signal to hippocampy would be inert (no consumer). A217's live-smoke run found the concrete cost of that: `classify_domain()` can't tell "never tried" from "tried repeatedly, confirmed inert" — both read `DISORDER`. Audit whether a cheap fix exists, ARC-side only, before considering any hippocampy schema change.

## Technical approach

### 1. Read primary sources first

`backlog/A213.md` Outcome, `backlog/A217.md` Outcome (both already written and accurate — do not re-derive). `agents/arc4/annatar_signals.py::compute_cycle_signals` in full — confirm exactly what graph calls it already makes for the current anchor (`fetch_entity_neighborhood` is confirmed; check whether `fetch_per_action_evidence`-shaped reward-counter data for the anchor's `action_id` is reachable there today, or would require adding a new call).

### 2. Determine the cheapest sufficient fix

Preference order, cheapest/least-risky first:
- **(a) ARC-side only, no new graph query.** If `compute_cycle_signals` (or a caller one level up, e.g. `run_annatar_cycle` in the same file) already has access to reward-counter data for this anchor's action_id via a call already being made elsewhere in the same cycle, reuse it — do not add a new `fetch_per_action_evidence` call solely for this if one isn't already happening nearby.
- **(b) ARC-side only, one new graph query.** If reward-counter data isn't already reachable, decide whether adding one `fetch_per_action_evidence`-style call specifically for this purpose is worth the extra round-trip per cycle, or whether that cost outweighs the benefit (this puzzle's puzzles run maybe 6-20 steps total — one extra call per cycle is not free, reason about it honestly rather than assuming it's negligible).
- **(c) Hippocampy-side schema addition.** Only if (a)/(b) don't work — write a `docs/handoff/B278-*.md` note (mirror `docs/handoff/B278-victory-condition-node-creation.md`'s format) describing exactly what's needed and why, following A215's established cross-repo handoff discipline. Do not implement ARC-side code that assumes a schema addition exists before it's confirmed to exist.

### 3. If a fix lands, keep `classify_domain`'s existing shape unless evidence says otherwise

The four-domain shape (`DISORDER`/`CONVERGED`/`COMPLEX`/`CHAOTIC`) was deliberately kept simple in A217. Prefer folding "confirmed inert" into the existing `CHAOTIC` value (it already means "evidence exists, nothing survived as a governing constraint" — a reliably-no-effect entity fits that description) over adding a fifth domain value, unless doing so would lose a distinction that actually matters for `transition()`'s DEEPENING-patience decision. Reason through this explicitly in the Outcome section rather than defaulting to "add a new enum value" for its own sake.

## Concrete file changes

| File | Change |
|------|--------|
| `backlog/A218.md` | Outcome section documents the finding either way |
| `agents/arc4/annatar_signals.py` (only if ARC-side fix) | `compute_cycle_signals` gains the reward-counter read |
| `agents/arc4/annatar_state_machine.py` (only if the four-domain shape needs to change) | `classify_domain`'s signature/logic |
| `tests/test_a218_*.py` (new, only if a fix lands) | Coverage matching whichever path was taken |
| `docs/handoff/B278-*.md` (new, only if hippocampy-side) | Schema ask |

## Validation commands

```bash
# If a fix lands:
.venv/bin/python -m pytest tests/test_a218_*.py -v
.venv/bin/python -m pytest tests/ -q
make test-a
make test-all
# If no fix lands:
make test-a
make test-all
```

## Assumptions/defaults

- Same discipline as every audit card in this family (A209/A212/A214/A215): if the evidence doesn't clearly support a fix, document why and leave the code alone.
- Do not reopen A213's core finding (`record_rule` genuinely does nothing with an empty `candidate_signatures` list) — that's settled. This card is about a *different*, narrower question.
