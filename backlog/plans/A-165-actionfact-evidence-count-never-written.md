# Plan: A165 — `ActionFact.evidence_count`/`supports` Are Never Written by Any B278 Tool

## Context

`agents/arc4/graph_queries.py::fetch_per_action_evidence` (fixed by A162) correctly reads `falsified_count` into `contradictions` now, but `attempts` and `supports` remain permanently `0` — not because of a client-side field-name mismatch (A162's category), but because hippocampy never writes to `ActionFact.evidence_count` from any tool (confirmed by grepping the entire `campy/` tree), and no tool returns a `supports`/`support_count`-shaped field for actions at all.

Separately, `arc_record_action_effect` *does* correctly increment `ActionFact.observation_count` on every call — but `arc_get_action_evidence` surfaces that value under the key `steps_used`, not `observation_count`, and the ARC client's `fetch_per_action_evidence` never reads `steps_used` at all. This is a genuine, immediately-actionable fix independent of anything hippocampy needs to do.

## Step 0: Decide scope (gate)

Two independent pieces, decide whether to do both, either, or neither this round:

1. **Client-side (`steps_used` → `attempts` fallback)** — no hippocampy dependency, low risk, immediately fixes the misleading `attempts: 0` context sent to the LLM for actions with real attempt history.
2. **Hand-off doc for `evidence_count`** — files the deeper gap (server never populates the field the schema declares and the read tool expects) so hippocampy can decide whether to wire a write path or the field is genuinely vestigial.

Recommendation: do both — they're cheap and independent. (1) is a one-line fix + test. (2) is documentation only, no runtime risk.

## Implementation

### 1. Client-side `steps_used` fallback

In `agents/arc4/graph_queries.py::fetch_per_action_evidence`, current `attempts` line (post-A162):

```python
"attempts": int(result.get("attempts", result.get("attempt_count", result.get("evidence_count", 0))) or 0),
```

becomes:

```python
"attempts": int(result.get("attempts", result.get("attempt_count", result.get("evidence_count", result.get("steps_used", 0)))) or 0),
```

`steps_used` checked last — it's the least-specific-sounding key but the only one that's actually populated by the real server today; keep `evidence_count` ahead of it in the chain in case hippocampy ever does wire a real write path per the hand-off below (that would then take priority, which is correct — `evidence_count` is the more semantically precise field if it ever becomes real).

### 2. Hand-off doc

New `docs/handoff/B278-action-evidence-count-never-written.md`, matching `docs/handoff/B278-graph-evidence.md`'s structure:

- **Summary**: `ActionFact.evidence_count` is declared in schema, read by `arc_get_action_evidence`, never written by any tool (`arc_record_action_effect` only sets `observation_count`; `arc_record_reward_prediction_error` only sets `confidence`/`falsified_count`). `supports`/`support_count` have no server-side representation for actions at all (unlike hypotheses, which do have `evidence_count` tracked via `arc_confirm_hypothesis`/`arc_contradict_hypothesis` — a *different* node type, easy to conflate).
- **Reproduction**: raw MCP calls — `arc_record_action_effect` 3x for the same `task_id`/`action_id`, then `arc_get_action_evidence` — show `evidence_count: 0` while `steps_used` (which is actually `observation_count` under a different key) correctly reads `3`.
- **Where to look**: `campy/brain/thalamus/tools/arc_queries.py::arc_record_action_effect` (~L292-326) — its `SET` clause only touches `observation_count`; the ask is either (a) also increment `evidence_count` there (if it's meant to mean the same thing as `observation_count`, in which case one of the two fields is redundant and should probably be reconciled/removed rather than both kept), or (b) clarify what `evidence_count` is semantically supposed to diverge from `observation_count` to represent, so a correct write path can be added.
- **Ask**: either wire a write path, or confirm the field is vestigial/deprecated so the ARC-side schema expectations can be updated to stop trying to read it.

## Tests

New/updated in `tests/test_a162_fetch_per_action_evidence_field_mismatch.py` (extend, same file, same root problem area) or a new `tests/test_a165_action_evidence_steps_used_fallback.py` — either is fine, prefer extending the existing A162 file since it's the same method:

1. `test_steps_used_fallback_populates_attempts_when_evidence_count_absent` — stub returns `{"falsified_count": 2, "steps_used": 5}` (no `evidence_count`, matching the real server shape) — assert `attempts == 5`.
2. `test_evidence_count_still_takes_priority_over_steps_used` — stub returns `{"evidence_count": 3, "steps_used": 5}` (both present, hypothetically) — assert `attempts == 3` (regression guard for if hippocampy ever wires a real `evidence_count`).

## Verify

```bash
.venv/bin/python -m pytest tests/test_a162_fetch_per_action_evidence_field_mismatch.py -v  # or new A165 file
make test-a
make test-all
```

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/graph_queries.py` | `fetch_per_action_evidence`'s `attempts` fallback chain gains `steps_used` |
| `tests/test_a162_fetch_per_action_evidence_field_mismatch.py` (or new A165 file) | 2 new tests |
| `docs/handoff/B278-action-evidence-count-never-written.md` | New hand-off doc |

## Risks

- Very low for the client-side fix (additive fallback, same pattern as A160-A162, all proven safe).
- The hand-off doc has zero runtime risk — documentation only.
