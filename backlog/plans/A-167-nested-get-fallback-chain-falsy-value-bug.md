# Plan: A167 — Fix A165's Inert `steps_used` Fallback (Nested `.get` Chain Bug)

## Context

`agents/arc4/graph_queries.py::fetch_per_action_evidence`'s `attempts` field uses a nested `.get(key, default)` fallback chain. `dict.get(key, default)` only evaluates `default` when `key` is absent — it returns the stored value even if that value is falsy (`0`). The real server response always includes an explicit `"evidence_count": 0` key (established by A165), which sits before `steps_used` in the chain, so the chain short-circuits at `0` and never reaches `steps_used`'s real data. Confirmed live: `raw.steps_used: 3` present alongside normalized `attempts: 0`.

## Implementation

### 1. Rewrite the `attempts` fallback as an `or`-chain

In `agents/arc4/graph_queries.py::fetch_per_action_evidence`:

```python
"attempts": int(result.get("attempts", result.get("attempt_count", result.get("evidence_count", result.get("steps_used", 0)))) or 0),
```

becomes:

```python
"attempts": int(result.get("attempts") or result.get("attempt_count") or result.get("evidence_count") or result.get("steps_used") or 0),
```

`or`-chaining treats "key absent", "key present with `None`", and "key present with `0`" identically — all fall through to the next candidate. This is the correct semantics here: none of these fields ever carry a meaningful distinction between "explicitly zero" and "not provided" for a monotonically-increasing attempt counter.

### 2. Reproduce the exact live bug in a test

New test in `tests/test_a162_fetch_per_action_evidence_field_mismatch.py` (same file as the rest of the `fetch_per_action_evidence` fallback-chain tests):

```python
def test_evidence_count_present_but_zero_still_falls_through_to_steps_used(self):
    """A167: the real server always includes evidence_count:0 explicitly (A165) --
    a nested .get(key, default) chain stops there and never reaches steps_used.
    This is the exact live-observed shape; A165's own test used a fixture without
    evidence_count present at all, which didn't reproduce the bug."""
    port = _port({"evidence_count": 0, "falsified_count": 3, "steps_used": 3})
    assert port.fetch_per_action_evidence("ACTION1")["attempts"] == 3
```

Also re-verify (don't just assume) the two existing A165 tests still pass with the `or`-chain rewrite — they should, since `or`-chaining is a strict superset of what nested `.get` handled correctly.

### 3. Decide and document the `evidence_count` priority tradeoff

With `or`-chaining, a hypothetical *genuine* future `evidence_count: 0` (real signal, not the current always-zero placeholder) would now fall through to `steps_used` instead of being trusted as "really zero." Document in the card's Resolution that this is an acceptable tradeoff: `steps_used` is real, working, monotonically-accurate data today; a future genuine `evidence_count` would in practice never legitimately be `0` if `steps_used` (which increments on every recorded effect) is also nonzero, so the two should never meaningfully diverge once `evidence_count` is real.

## Verify

```bash
.venv/bin/python -m pytest tests/test_a162_fetch_per_action_evidence_field_mismatch.py -v
make test-a
make test-all
```

Live confirmation (required — this bug was specifically found by live-smoke, not unit tests, so the fix needs live confirmation too):

```bash
CAMPY_MCP_CMD="../hippocampy/.venv/bin/python -m campy.adapters.mcp_server" \
  PYTHONPATH=. .venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 15
grep -o '"attempts": [0-9]*' <log> | sort | uniq -c
```
Confirm nonzero `attempts` values appear for actions with nonzero `raw.steps_used`.

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/graph_queries.py` | `fetch_per_action_evidence`'s `attempts` chain rewritten from nested `.get` to `or`-chaining |
| `tests/test_a162_fetch_per_action_evidence_field_mismatch.py` | +1 test reproducing the exact live-observed response shape |

## Risks

- Very low — `or`-chaining is a strict superset of correct nested-`.get` behavior for this specific field (all existing correct cases still resolve the same way; only the previously-broken present-but-zero case changes, and only for the better).
- Worth a quick audit (separate follow-up, not blocking this card) of whether `contradictions`/`supports`/`confidence`'s nested chains could ever hit the same bug if the server's response shape changes in the future — today they're confirmed safe because the intermediate keys are genuinely absent, but that's an empirical fact about the current server, not a structural guarantee.
