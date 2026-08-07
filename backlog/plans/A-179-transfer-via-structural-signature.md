# Plan: A179 — Transfer via Structural Rule Signature

## Context

`action_signature` (hash of which buttons exist) is a weak similarity key — confirmed live: two mechanic records with identical signatures both describe unrelated games as "space archetype." Real transfer needs to key on what a rule *claims*, not what buttons a game happens to expose.

## Implementation

### Structural fingerprint

Over A177's canonicalized rule signatures, define a fingerprint invariant to literal color/action-id values but sensitive to shape — e.g. normalize "color 3 → color 7 near clicked cell" and "color 1 → color 9 near clicked cell" to the same fingerprint if the *relational* structure (single-color-to-single-color, proximity-triggered) matches.

### Retrieval

New query: given the current game's observations, find rules from other `task_id`/`game_id`s whose fingerprint matches. Surface as low-confidence hypotheses (explicitly weighted below in-game-confirmed rules) rather than facts.

## Tests

New `tests/test_a179_structural_transfer.py`:

1. Fingerprint invariance: same underlying pattern with different colors/action-ids → same fingerprint.
2. Fingerprint discrimination: genuinely different mechanics → different fingerprints (no false-positive collisions).
3. Cross-game retrieval integration: a rule recorded for game A surfaces as a hypothesis when planning for a different, structurally-similar game B; does not surface for an unrelated game C.
4. Confidence weighting: transferred hypotheses carry lower initial confidence than in-game-confirmed rules (regression guard against over-trusting transfer).

## Verify

```bash
.venv/bin/python -m pytest tests/test_a179_structural_transfer.py -v
make test-a
make test-all
```

Live confirmation: run `--live-smoke` across two different games in one session (or two separate runs, same manifest slot), check whether a rule learned in the first game's graph surfaces as a hypothesis when planning for the second.

## Files Modified

| File | Change |
|------|--------|
| New module or extension of A177's rule-extraction code | Structural fingerprinting |
| `agents/arc4/goal_resolver.py`/`plan_generator.py` | Consume transferred hypotheses with appropriate confidence discount |
| `docs/handoff/B278-transfer-via-structural-signature.md` | New hand-off doc |
| `tests/test_a179_structural_transfer.py` | New tests |

## Risks

- Weakest-evidence card in the sequence — only as good as A177's rules. Land last, after identity/persistence/rules are proven solid within a single game.
- Real risk of false-positive transfer (superficially similar mechanics that aren't actually the same) — the confidence-discount requirement is the guardrail, not a nice-to-have.
