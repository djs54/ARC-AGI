# Handoff: `arc_check_action_gate`'s veto trusts a falsification counter ARC could corrupt, and now can't fully un-corrupt

**For:** hippocampy / Campy owner (B278 owns brain internals; ARC consumes across the MCP seam)
**From:** ARC_AGI A232 investigation (2026-09-01)
**Status:** root cause identified and fixed on the ARC side going forward, plus a defensive ARC-side mitigation for already-corrupted historical data; a server-side improvement is still recommended for the reasons below

## Summary

`arc_check_action_gate` (`campy/brain/thalamus/tools/arc_queries.py`, ~L733-772) vetoes an action (`go = False`) when `falsification_count >= 3 and untested_available`. `falsification_count` is `ActionFact.falsified_count`, incremented by `arc_record_reward_prediction_error` (~L568-611) whenever `error = actual_reward - predicted_reward < -0.3`.

Until this fix, ARC's client (`agents/arc4/graph_queries.py::record_evaluation`) called `arc_record_reward_prediction_error` with a **hardcoded `predicted_reward = 1.0`** and `actual_reward` derived from whole-puzzle-progress (`meaningful_progress`), not from whether the action's own predicted effect was confirmed. Since `predicted_reward` never varied, `error` could only ever be `0.0` (progress, no-op) or `-1.0` (no progress, falsification) — the confidence-boosting branch (`error > 0.3`) was mathematically unreachable. In practice this call **only ever falsified or no-op'd**, never rewarded, regardless of whether the action had a real, causally-confirmed grid effect. Ordinary exploratory actions (most actions, by design — exploring isn't solving) falsified on nearly every attempt.

Confirmed live on `lf52-271a04aa`, cross-referencing `fetch_rules_for_action` (the real, correctly-functioning Rule-graph evidence source, A177/B278) against `fetch_per_action_evidence` (`ActionFact`, what `arc_check_action_gate` actually reads) for the same actions at the same moment:

| Action | `fetch_rules_for_action` | `fetch_per_action_evidence` |
|---|---|---|
| `ACTION2` | 1 real, unfalsified rule, confidence 0.5 | `confidence: 0.0`, `falsified_count: 1`, `attempts: 1` |
| `ACTION3` | 1 real, unfalsified rule, confidence 0.5 | same shape, 100% "falsified" |
| `ACTION4` | 1 real, unfalsified rule, confidence 0.5 | same shape, 100% "falsified" |
| `ACTION7` | 1 real, unfalsified rule, confidence 0.5 | same shape, 100% "falsified" |
| `ACTION6` | 5 real, unfalsified rules, confidence up to **1.0** | `confidence: 0.0`, `falsified_count: 82`, `attempts: 82` |

`falsified_count == attempts` for every action checked, every time, even against real working Rule-graph evidence for the identical action. Since `arc_check_action_gate`'s veto threshold is `>= 3`, essentially any action attempted 3+ times with any untested alternative available got actively vetoed — not a telemetry artifact, a live decision-changing bug.

## Where to look

`campy/brain/thalamus/tools/arc_queries.py`:

- `arc_record_reward_prediction_error` (~L568-611): `error = actual_reward - predicted_reward`; `error < -0.3` → `falsified_count += 1` and confidence decays toward 0; `error > 0.3` → confidence increases. The positive branch was unreachable given ARC's old fixed `predicted_reward = 1.0` — see ARC-side fix below.
- `arc_check_action_gate` (~L733-772): `go = False` when `falsification_count >= 3 and untested_available`, reading `ActionFact.falsified_count` directly with no cross-check against any other evidence (e.g. `Rule`/`PREDICTS`/`FALSIFIED_BY`, the graph objects `get_rules_for_action` reads) before trusting it.

## ARC-side fix already shipped (no action needed from you to unblock this)

1. **`record_evaluation` no longer calls `arc_record_reward_prediction_error` at all.** There is no principled per-step numeric reward signal in ARC-AGI-3 to compute a meaningful prediction error from (whole-puzzle "did this solve it" and per-action "was the predicted effect confirmed" are different questions); rather than invent one, the call is removed. `arc_record_action_effect` (which never touches `confidence`/`falsified_count` server-side) is unaffected and still fires every step. Going forward, `ActionFact.falsified_count` stops accumulating this corruption entirely.
2. **`plan_generator.py`'s candidate `graph_evidence` metadata** (the only thing `graph_grounded`/`graph_informed` telemetry ever reads) now blends `fetch_rules_for_action`'s live, unfalsified rule confidences in directly, not just into the internal ranking score — so a real working rule is visible to those KPIs even where `fetch_per_action_evidence`'s own numbers look bad (historically corrupted or otherwise).
3. **`plan_vetter.py`'s graph gate now defends itself against a stale/corrupted denial**: before letting a `check_action_gate` `allowed=False` result veto a candidate, it cross-checks `fetch_rules_for_action` for real, live, unfalsified, positive-confidence rule evidence for the same action. If that evidence exists, the denial is overridden (tracked in the vet decision's metadata as `graph_gate_overridden: true`) rather than trusted blindly. This is a defensive mitigation only, scoped to what ARC can see and control — it does not touch or fix `ActionFact.falsified_count` itself.

## What's still a server-side gap (the actual ask)

Removing ARC's write stops **new** corruption, but does not retroactively repair **already-accumulated** `ActionFact.falsified_count` values for tasks that were in flight before this fix landed, and ARC's defensive override (item 3 above) only fires when live Rule-graph evidence happens to exist for the exact same action — it's a mitigation, not a cure, and gives no help for an action that's falsely accumulated a stale count with genuinely no Rule-graph evidence yet either way. Two directions worth considering, either or both:

1. **`arc_check_action_gate` cross-checks live Rule evidence before trusting a stale falsification count** — e.g. don't veto (or lower confidence in the veto) when `get_rules_for_action` shows a real, unfalsified rule with meaningful confidence for the same action, mirroring what ARC's own defensive fix now does client-side, but authoritative and consistent for every ARC consumer rather than opt-in per client.
2. **`arc_record_reward_prediction_error` requires a real, non-hardcoded reward signal** rather than accepting whatever a client sends — e.g. reject or ignore writes where `predicted_reward` is a suspiciously-constant value across many calls for the same task, or require a genuine bounded prediction (not a proxy for whole-episode success) before allowing the falsification branch to fire at all. This would have caught ARC's bug at the seam instead of relying on the consumer to self-diagnose it.

No urgency for a specific implementation — either direction (or a different one you prefer) closes the historical-data gap ARC's own fix can't reach. ARC has already stopped contributing to the problem and added what defense it can from its side of the seam.

## How ARC will know it's fixed

If `arc_check_action_gate` (or `arc_record_reward_prediction_error`) starts cross-checking Rule-graph evidence server-side, ARC's existing `tests/test_a232_graph_gate_override.py` and the live-verification described in `backlog/A232.md`'s Outcome section remain the reference point: an action attempted 3+ times with real, confirmed, unfalsified Rule-graph evidence should no longer show `falsified_count` climbing to match `attempts` 1:1, and `arc_check_action_gate` should not veto it purely on that stale count.
