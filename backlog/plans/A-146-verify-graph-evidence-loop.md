# Plan: A146 — Verify Graph Evidence Loop Against B278

## Context

ARC_AGI consumes the ARC graph tools over the MCP seam; the server side is B278 in the sibling `hippocampy` repo. The consumer wiring:

- `agents/arc4/graph_queries.py`:
  - `fetch_per_action_evidence(action_id)` → `arc_get_action_evidence` → reads `supports`, `contradictions`/`contradiction_count`, `confidence`, `attempts` (lines ~107-119); returns zeros on any failure.
  - `record_evaluation(evaluation)` → `arc_record_action_effect` with `effect_match`, `observed_kind`, `effect_kind` (lines ~245-255, added by A138).
- Consumers of the evidence: `agents/arc4/plan_generator.py` (`_build_candidates` applies a contradiction penalty when `contradictions > supports`), `agents/arc4/evaluator.py` (A135 causal-path override).

B278 symptom reported by the Campy agent: `falsified_count` returns 0 where tests expect 3 — the graph query the implementation issues doesn't match the mock fixtures, so contradiction/falsification counts never populate.

This card is **consumer-side only**. It must not modify or vendor B278 (MCP seam, CLAUDE.md non-negotiable #1).

## Implementation Steps

### Step 1: Consumer-side contract test (skipped without MCP)

**File:** `tests/test_a146_graph_evidence_contract.py`

```python
import os
import pytest

pytestmark = pytest.mark.requires_mcp  # skipped unless CAMPY_MCP_CMD is set (A142 conftest gate)


def test_falsifications_surface_as_contradictions():
    """Record N falsifications for an action, then read them back as contradictions.

    This is the exact B278 failure mode: falsified_count==0 where it should be N.
    Until B278 lands, this test documents the expected contract.
    """
    # Build the real MCP-backed GraphQueryPort the runtime uses (see arc_runtime/bundle.py
    # for the construction pattern). Record an action effect as falsified N times via
    # arc_record_action_effect, then assert fetch_per_action_evidence(action)["contradictions"] == N.
    ...
```

Use `arc_runtime/bundle.py` / `graph_queries.py` to see how the production `GraphQueryPort` is constructed against the MCP client; reuse that wiring rather than hand-rolling MCP calls. Keep the test small and explicit about expected-vs-actual.

### Step 2: Document the dependency

- **ARCHITECTURE.md**: in the ARC v2 / MCP-seam section, add a line naming B278 (hippocampy) as the owner of the ARC graph tools listed in the A146 card, and link A146 as the consumer-side verification.
- Confirm CLAUDE.md:22 already references B278 (it does) — no change needed there.

### Step 3: Hand-off note for the B278 owner

Append a short section to this plan (or a `docs/handoff/B278-graph-evidence.md`) capturing: the 4 failing tests, the `falsified_count==0` symptom, the query-vs-fixture drift hypothesis, and the list of ARC_AGI consumers (A135 penalty, A138 confirmation recording) that depend on the fix. This is the artifact to send to whoever owns hippocampy.

### Step 4: Closure (when B278 lands)

```bash
CAMPY_MCP_CMD="../hippocampy/.venv/bin/python -m campy.adapters.mcp_server" \
  .venv/bin/python -m pytest tests/test_a146_graph_evidence_contract.py -q
# then a live smoke; inspect submission_results_single.world_model.live.jsonl for
# a non-trivial supports/contradictions mix (not all zeros)
```

Flip the card to complete only when the contract test passes against a committed B278.

## Files Modified

| File | Change |
|------|--------|
| `tests/test_a146_graph_evidence_contract.py` | New, `requires_mcp` contract test |
| `ARCHITECTURE.md` | Note B278 ownership + link A146 |
| `docs/handoff/B278-graph-evidence.md` (optional) | Hand-off note |

## Conflict Note (for fan-out)

Independent of all other open cards (adds a test + docs). No runtime code changes.

## Risks

- The test depends on a live MCP daemon; it is `requires_mcp`-gated so CI/local default runs skip it cleanly (A142 conftest).
- Resist scope creep into fixing B278 here — that is the sibling repo's work.
