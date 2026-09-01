# A235 — Graph-Grounded Anchor Productivity: Plan

## Card metadata

- Card: `backlog/A235.md`
- Depends on: A183 (`world_model_edge_writes`/`node_writes`), A207/A230 (whole-episode-futility routing), A217/A218 (Cynefin domain, the rejected alternative signal)

## Design (settled direction, one real timing subtlety flagged for Track A)

Confirmed by direct read of `agents/arc4/annatar_signals.py::run_annatar_cycle` (~line 452-502) before writing this plan:

- Anchor creation block (`if anchor is None:`, ~454-482) builds a fresh anchor dict with `"any_progress": False`.
- `signals = compute_cycle_signals(...)` (~485-500) is called every cycle, already computing `signals.domain` — unused for this purpose.
- `anchor["any_progress"] = anchor.get("any_progress", False) or bool(signals.meaningful_progress)` (line 502) is the entire "was this productive" definition today.

### The fix

Add `anchor["edge_writes_at_start"] = state.world_model_edge_writes` to the anchor-initialization block (alongside the existing `"any_progress": False` field). Then, alongside the existing `meaningful_progress` OR-in at line 502, add a second OR-in:

```python
graph_grew = state.world_model_edge_writes > anchor.get("edge_writes_at_start", state.world_model_edge_writes)
anchor["any_progress"] = anchor.get("any_progress", False) or bool(signals.meaningful_progress) or graph_grew
```

### Track A: resolve the first-cycle timing subtlety before implementing

**Real issue, confirmed by tracing the call sequence, not assumed:** `run_annatar_cycle` executes *after* `evaluate` in the same cycle (confirmed via `workflow.py`'s call order). This means when a *brand-new* anchor is created (the `anchor is None` branch), the snapshot `state.world_model_edge_writes` taken at that moment *already includes* whatever THIS SAME cycle's `evaluate` just wrote. So a check of "has it grown since `edge_writes_at_start`" can never register growth attributable to an anchor's very first cycle — only cycle 2+ on the same anchor (DEEPENING/RETRY) can detect it, since by then the baseline was captured before that later cycle's own write.

Decide one of:
1. **Accept this as a stated, documented limitation** — the guard still improves meaningfully for any anchor investigated across 2+ cycles (the common case for DEEPENING/RETRY, which is exactly when the streak has time to accumulate anyway — a single-cycle anchor immediately advancing was never going to threaten the streak on its own). Document this precisely in the code comment and the card's Outcome, don't leave it implicit.
2. **Fix it properly**: have `workflow.py` snapshot `state.world_model_edge_writes` *before* calling `evaluate` each cycle (a one-line addition at the point `perception_payload`/`execution_payload` are already being tracked) and pass that pre-evaluate snapshot into the `annatar(...)` call as a new keyword (mirroring `readiness_report`/`resolve_report`'s existing pattern) — then `run_annatar_cycle` can correctly attribute even a brand-new anchor's very first cycle's own graph growth. More correct, more invasive (touches `workflow.py`, `ports.py`'s `AnnatarPhase` protocol, `bundle.py`'s closure, same shape of change A230/A234 already made twice).

Investigate which is warranted — read how often a *single-cycle* anchor (one that never reaches DEEPENING/RETRY) is the one that actually causes real graph growth in a live run before deciding effort is justified. Option 1 is the pragmatic default unless live data shows single-cycle anchors are commonly where real graph growth happens uncredited.

### Why `world_model_edge_writes` (not `signals.domain`)

Already investigated and settled in the card itself: `signals.domain != DISORDER` was considered and rejected — by the time goal-directed play starts (post-readiness-gate), most anchors are *already* non-DISORDER (that's what `readiness_status()`'s READY condition means), so a static domain check would be trivially true almost immediately, defeating the guard's actual purpose. `world_model_edge_writes` growing captures *change during this anchor's own investigation*, which is what "was this productive" actually needs to mean.

### Node writes vs. edge writes

Investigate which better fits "real causal learning" before committing: `world_model_edge_writes` (A183: increments in `evaluator.py` on confirmed rule/transition-evidence writes — CONFIRMED_BY/FALSIFIED_BY/PREDICTS edges, causal claims) is the more precise fit conceptually. `world_model_node_writes` (increments in `perceive.py` too, on GridEntity/GridSnapshot writes) is broader and includes routine perception bookkeeping that isn't really "learning" in the sense this card cares about. Default to `world_model_edge_writes` alone unless investigation shows a real reason to include node writes too.

## Implementation approach

### Files

- Modify: `agents/arc4/annatar_signals.py` — `run_annatar_cycle`'s anchor-creation block and the `any_progress` line.
- Possibly modify (only if Track A picks Option 2): `agents/arc4/workflow.py`, `agents/arc4/ports.py`, `arc_runtime/bundle.py`.
- Test: extend `tests/test_a202_annatar_orchestrator_integration.py`'s `TestRunAnnatarCycleWholeEpisodeFutility` class (it already owns this exact area).

### TDD

- New test: an anchor whose `meaningful_progress` stays `False` across all its cycles, but `state.world_model_edge_writes` grows between cycle 1 and cycle 2 of the SAME anchor (simulating real graph learning happening) — confirm `any_progress` becomes `True` and the streak does NOT increment for that anchor's conclusion.
- Regression test: the guard's original purpose survives — an anchor with `meaningful_progress=False` AND `world_model_edge_writes` genuinely flat across all its cycles still correctly counts as unproductive, and 3 such anchors in a row still terminates the episode. Don't let this fix silently defeat the guard entirely.
- Regression: every existing `TestRunAnnatarCycleWholeEpisodeFutility` test continues to pass unchanged unless the fix's own logic requires a stated, reasoned update (e.g., if a fixture needs `world_model_edge_writes` added to stay flat/unchanged across its cycles to preserve its original "unproductive" assertion).

### Validation commands

```bash
.venv/bin/python -m pytest tests/test_a202_annatar_orchestrator_integration.py -v
make test-a
make test-all
```

### Live-verify

Same environment/discipline as every prior card this investigation (`.venv` worktree symlink if isolated, `CAMPY_MCP_CMD` absolute path). Run a live smoke on a puzzle likely to produce multi-cycle anchors with real graph growth (a puzzle similar in shape to `ar25-0c556536` — many entities, several genuinely testable actions) and confirm, via direct graph query plus the trace, that an anchor whose investigation produced real Cynefin-domain movement does NOT get counted toward the unproductive streak, and that the episode runs longer / makes it further into goal-directed play than a pre-fix run would have on a comparable puzzle. Also confirm the negative case still works: if a live run happens to hit a genuinely dead puzzle (unlikely to arrange deliberately, but note if one is observed), the streak still fires and terminates correctly.

## Assumptions/defaults

- If Track A's first-cycle timing question is resolved via Option 1 (documented limitation), that is a complete, acceptable outcome for this card — don't feel obligated to escalate to Option 2's larger change unless live data actually shows it's needed.
- `world_model_edge_writes` alone (not also `node_writes`) is the default signal unless investigation finds a concrete reason to include node writes too.
