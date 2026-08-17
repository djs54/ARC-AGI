# Plan: A187 — Only Decay the Positive Signal, Not the Contradiction Penalty

## Card metadata

- ID: A187
- Priority: P1
- Layer: ARC runtime
- Dependencies: A182, A185

## Summary

`plan_generator.py::_build_candidates`'s tested-action branch multiplies the *combined* score (`graph_positive_score - graph_contradiction_penalty`, A185's split) by `repeat_decay_factor ** attempts`. Applied to a negative value, this shrinks the falsification penalty toward zero as `attempts` grows, instead of leaving it (or letting it grow) — confirmed live: an action falsified four times outscored one falsified once, with an exact reproduction of the numbers in `backlog/A187.md`.

## Technical approach

1. Read `agents/arc4/plan_generator.py::_build_candidates`'s current tested-action branch in full before editing (it has been modified twice already tonight, by A182 and A185 — confirm the exact current line numbers and variable names rather than assuming).
2. Change the score computation so `repeat_decay_factor ** attempts` only multiplies `graph_positive_score`, and `graph_contradiction_penalty` is subtracted at full magnitude afterward:

```python
decay = self._limits.repeat_decay_factor ** attempts
score = graph_positive_score * decay - graph_contradiction_penalty
score -= min(self._limits.repeat_attempt_penalty * attempts, 0.18)
```

3. Do not change the `is_untested` branch (A185's `withhold_family_penalty` logic) — this card is scoped to the *tested* (`else`) branch only.
4. Do not change `graph_contradiction_penalty_applied`'s computation or how it gates the local `action_falsification_counts` fallback penalty (A182's logic) — that's already correctly undecayed and orthogonal to this card.
5. Double check `_voi_bonus` (A178, used in the `is_untested` branch) is untouched — this card does not affect untested-action scoring at all, only the repeat/decay path for actions that have themselves been attempted.

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/plan_generator.py` | `_build_candidates`: decay only `graph_positive_score`, subtract `graph_contradiction_penalty` undecayed |
| `tests/test_a187_decay_does_not_forgive_repeated_falsification.py` (new) | Regression coverage (see Tests) |

## Tests

New `tests/test_a187_decay_does_not_forgive_repeated_falsification.py`:

1. This card's exact live reproduction: construct a graph port whose `evidence_contradictions` grows with each call to mirror `attempts=2,3,4` (matching the card's reverse-engineered base values, or a simplified equivalent with a fixed per-attempt contradiction increment), and assert the resulting score is monotonically non-increasing (does not improve) as `attempts` grows — the direct inverse of the old (buggy) behavior.
2. Direct comparison: an action falsified once scores *better* than an action falsified four times, when both draw from the same per-failure penalty magnitude — this is the core assertion the live incident demonstrated failing.
3. Regression guard: a genuinely positive family signal (`evidence_confidence` or rule confidence, no contradictions) still fades with `attempts` as originally designed — decay must still apply to the positive case, only withheld from the negative one.
4. Regression guard: `is_untested` (first-attempt) scoring is completely unaffected — reuse or adapt A185's existing untested-path tests to confirm no interaction.

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a187_decay_does_not_forgive_repeated_falsification.py -v
make test-a
make test-all
```

Live confirmation: `campy status` health check, then `make smoke` (self-capped). Best-effort — depends on an episode where the same action is falsified multiple times in a row with real graph contradiction evidence, same caveat as A182/A184/A185. If the unit tests are the primary verification for this run, say so honestly rather than blocking on a specific live outcome.

## Assumptions/defaults

- The fix keeps decay's original purpose intact (fading an over-exploited positive signal) — it does not remove decay, only scopes what it's allowed to multiply.
- `graph_contradiction_penalty` remaining fully undecayed (not just "decayed less") is the chosen design — matches how A182's local-fallback penalty already behaves (also undecayed), for consistency between the two penalty sources.
