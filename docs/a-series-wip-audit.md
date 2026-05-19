# A-Series WIP Audit

Date: 2026-05-16
Branch audited: `codex/arc-agi-a-series-wip`
Baseline branch: `master`

## Summary

The A-series WIP branch is valuable but not merge-ready.

It contains a coherent world-model direction with many passing isolated test groups, but it also mixes:

- production runtime changes
- test-only compatibility shims
- backlog/card material
- exploratory A092-A095 notes
- partially aligned behavior expectations

Recommended handling:

- Keep the proven foundations and green isolated modules.
- Fix the clustered planner/mechanic-prior and compatibility-shim failures before merge.
- Archive exploratory notes and any implementation that introduces shadow memory/import-boundary risk.

## Validation Snapshot

Commands were run from an isolated worktree at `/tmp/arc_agi_audit` using the ARC_AGI virtualenv:

```bash
/Users/djshelton/Desktop/GitProjects/ARC_AGI/.venv/bin/python -m pytest -q ...
```

Grouped results:

| Group | Result | Notes |
|---|---:|---|
| A039-A047 | 26 passed | Keep candidate |
| A059-A065 | 28 passed, 1 failed | Mostly keep; fix MCP timeout status expectation |
| A066-A072 | 15 passed | Keep candidate |
| A073-A078 | 20 passed, 1 failed | Keep foundation; fix `pixel_churn` vs `state_transition` classifier semantics |
| A079-A085 | 33 passed, 6 failed | Fix before merge; planner/prior/progress snapshot semantics are not aligned |
| A086-A091 | 66 passed, 1 failed | Mostly keep; fix single-action prior mapping |
| A092-A110 | 172 passed | Keep candidate after integration review |
| `tests/test_arc3_durable_runner.py tests/test_b185_failure_taxonomy.py` | 49 passed, 2 failed | Fix compatibility shim / seed path issues |
| Full suite | 1125 passed, 22 failed | Not merge-ready |

## Keep

These areas have strong isolated signal and should be preserved, but still merged in small slices.

### A039-A047 Smoke/Telemetry Regressions

Keep as a first extraction slice:

- `tests/test_a039_timeout_budget.py`
- `tests/test_a041_delta_reconciliation.py`
- `tests/test_a042_reproduce_false_positive.py`
- `tests/test_a043_pattern_match_static_confidence.py`
- `tests/test_a044_non_blocking_notify.py`
- `tests/test_a045_autopilot_wall_hit.py`
- `tests/test_a047_trajectory_scoring.py`

Reason:

- The group is green.
- These tests address concrete live-smoke regressions and are lower-risk than the world-model stack.

### A066-A072 Runtime Guardrails

Keep as an early extraction slice:

- `tests/test_a066_meaningful_progress_gate.py`
- `tests/test_a067_multi_action_macro_eligibility.py`
- `tests/test_a068_execute_memory_read_firewall.py`
- `tests/test_a069_hypothesis_falsification.py`
- `tests/test_a070_monotonic_terminal_progress.py`
- `tests/test_a071_sync_autopilot.py`
- `tests/test_a072_harden_solve_context.py`

Reason:

- The group is green.
- It reinforces hot-path memory firewalling and progress semantics.

### A073-A078 World-Model Foundation

Keep, but fix classifier drift first:

- `agents/arc3/world_model.py`
- `agents/arc3/world_model_compiler.py`
- `agents/arc3/world_model_planner.py`
- `agents/arc3/reasoning_controller.py`
- `benchmarks/arc3/world_model_eval.py`
- related tests A073-A078

Reason:

- 20/21 tests pass.
- The architecture direction is coherent: per-game causal graph, compiled world deltas, gated reasoning, graph-guided planner, and eval stream.

Blocking issue:

- `tests/test_a074_world_model_compiler.py::test_world_model_compiler_classification`
- Expected `pixel_churn`, actual `state_transition`.
- Decide the semantic rule: if the frame hash changes without object/terminal progress, should that be visual churn or generic state transition?

### A092-A110 Graph/Click/Route Extensions

Keep after integration review:

- `agents/arc3/scene_graph.py`
- `agents/arc3/mechanic_graph.py`
- `agents/arc3/goal_hypothesis.py`
- `agents/arc3/graph_transform.py`
- `agents/arc3/level_transfer.py`
- `agents/arc3/click_candidates.py`
- `agents/arc3/click_telemetry.py`
- related tests A092-A110

Reason:

- The group is green: 172 passed.
- It appears to be the strongest later-stage slice.

Merge caution:

- These files depend conceptually on the world-model foundation and should not be merged ahead of A073-A078.

## Fix

These areas should remain on the WIP branch until corrected.

### MCP Capability Missing vs Degraded Semantics

Failures:

- `tests/test_a059_memory_hot_path_latency.py::test_mcp_brain_client_timeout_fallback_label`
- `tests/test_a081_aggregate_mechanic_memory_transfer.py::test_mcp_brain_client_capability_missing`
- `tests/test_mcp_brain_client.py::test_mechanic_memory_missing_tools_degrade_cleanly`

Current mismatch:

- Tests expect either `status == "error"` or `status == "capability_missing"`.
- Implementation often returns `status == "degraded"`.

Recommendation:

- Standardize the contract:
- Transport/backend unavailable should be `degraded`.
- Tool absent/capability not available should be `capability_missing` and should not mark memory as degraded.
- Timeout can be `degraded` if that is now the desired contract, but then update old tests deliberately.

### Mechanic Prior Mapping Into Single Legal Action

Failures:

- `tests/test_a079_stall_classified_early_stop.py::test_cheap_probe_refreshes_planner_with_single_action_prior`
- `tests/test_a090_mechanic_prior_use_planner_ranking.py::TestMechanicPriorUsePlanning::test_single_action_prior_maps_mismatched_action_to_only_legal_action`

Current mismatch:

- Prior effect says `ACTION2`, available action is only `ACTION6`.
- Test expects prior transfer onto the only executable action.
- Planner returns an `ACTION6` candidate but drops `mechanic_prior_id`.

Recommendation:

- Keep the behavior if and only if single-action games intentionally translate aggregate action IDs onto the sole legal action.
- If yes, implement explicit provenance transfer and record `mechanic_prior_id`, `mechanic_prior_source`, and compatibility score.
- If no, rewrite tests and plan text to avoid unsafe prior transfer.

### Pattern-Correspondence Planner Requires Too Much Mock Surface

Failure:

- `tests/test_a081_aggregate_mechanic_memory_transfer.py::test_planner_prior_provenance`

Current mismatch:

- `WorldModelPlanner.select_next_candidate()` assumes the world model has `find_pattern_correspondence_candidates`.
- Lightweight test mock only exposes `get_active_hypotheses`.

Recommendation:

- Make planner feature-detect optional world-model methods.
- Pattern-correspondence ranking should gracefully return no candidates when that method is absent.

### Progress Snapshot Assumes Fully Initialized Runner

Failure:

- `tests/test_a080_world_model_eval_controller_metrics.py::test_progress_snapshot_uses_last_planner_selection_fallback`

Current mismatch:

- Test constructs `DurableARCRunner.__new__`.
- `_emit_progress_snapshot()` assumes `self.brain` exists.

Recommendation:

- Use `getattr(self, "brain", None)` before reading `inner`.
- Keep the snapshot helper robust for tests and degraded runtime states.

### Cheap Probe Decision Source

Failure:

- `tests/test_a082_deterministic_cheap_probe_action_path.py::test_cheap_probe_uses_deterministic_fallback_without_llm`

Current mismatch:

- Test expects `decision_source == "cheap_probe"`.
- Runtime returns `guard_override`.

Recommendation:

- Decide whether guard override is a policy layer above cheap probe.
- If both are true, represent both fields, e.g. `decision_source="cheap_probe"` and `guard_layer="guard_override"`.

### Multi-Action Churn Exhaustion Label

Failure:

- `tests/test_a085_multi_action_no_progress_gate.py::TestMultiActionChurnDetection::test_all_actions_churn_strategy_exhausts_after_probe_epochs`

Current mismatch:

- Test expects `world_model_decision == "strategy_exhausted"`.
- Runtime returns `multi_action_churn_exhausted`.

Recommendation:

- Prefer the more specific label internally, but expose a normalized `failure_class == "strategy_exhausted"` for summary/eval compatibility.

### SideQuest/HippoCampy Import Compatibility Shim

Failures:

- `tests/test_arc3_durable_runner.py::test_loop_worker_survives_error`
- `tests/test_submission_compliance.py::test_submission_runner_initialization`

Current mismatch:

- WIP adds local `mcp_engine/` shim files.
- The shim shadows the real sibling `mcp_engine` package and does not expose `mcp_engine.loop.step2_gist`.

Recommendation:

- Do not merge the local `mcp_engine/` shim as-is.
- Prefer fixing test environment imports through editable install / `PYTHONPATH` to the sibling HippoCampy repo.
- If a shim is needed for tests only, it should be complete enough to forward the whole namespace and must not ship as production ARC runtime code.

### Seed Path Resolution From Temporary Worktrees

Failure:

- `tests/test_arc3_durable_runner.py::test_upsert_lesson_round_trip`

Current mismatch:

- Test fallback uses `Path.cwd().parent / "hippocampy"`, which fails from `/tmp/arc_agi_audit`.

Recommendation:

- Resolve seed examples through package resources when possible.
- Otherwise search known repo roots, including `/Users/djshelton/Desktop/GitProjects/hippocampy/campy/data/GistSeedExamples.md`.
- Better: avoid direct path knowledge in ARC_AGI tests and use HippoCampy package APIs/resources.

## Archive

These should not be merged into `master` as active runtime code without review.

### Exploratory A092-A095 Root Notes

Archive or move under docs/research:

- `A092-A095_CODEBASE_SURVEY.md`
- `A092-A095_CODE_PATTERNS.md`
- `A092-A095_EXPLORATION_COMPLETE.md`
- `A092-A095_EXPLORATION_INDEX.md`
- `A092-A095_EXPLORATION_SUMMARY.md`
- `A092-A095_IMPLEMENTATION_CHECKLIST.md`
- `A092-A095_QUICK_START.md`

Reason:

- They are useful research artifacts but noisy at repo root.
- If retained, move to `docs/research/a092-a095/`.

### Local `mcp_engine/` Shim

Archive or remove:

- `mcp_engine/__init__.py`
- `mcp_engine/config.py`
- `mcp_engine/llm/__init__.py`
- `mcp_engine/llm/provider.py`
- `mcp_engine/loop/__init__.py`
- `mcp_engine/loop/orchestrator.py`

Reason:

- This risks violating the repo boundary by reintroducing a shadow memory package.
- It is incomplete and causes import failures.
- It conflicts with the rule that ARC_AGI should consume Campy over MCP or a clean external package, not own memory internals.

### Bulk Backlog Cards As One Commit

Do not merge all backlog cards/plans in one lump.

Reason:

- The branch adds A020, A038-A110 cards/plans in one shot.
- Many are useful, but they should be split by implementation slice and linked to tests/commits.

Recommendation:

- Keep cards/plans for green slices first.
- Park future cards in an archive/staging folder if they are not ready to govern active implementation.

## Recommended Split

### Commit 1: Low-Risk Smoke Regression Fixes

Scope:

- A039-A047 code/tests only.

Gate:

```bash
pytest -q tests/test_a039_timeout_budget.py tests/test_a041_delta_reconciliation.py tests/test_a042_reproduce_false_positive.py tests/test_a043_pattern_match_static_confidence.py tests/test_a044_non_blocking_notify.py tests/test_a045_autopilot_wall_hit.py tests/test_a047_trajectory_scoring.py
```

### Commit 2: Hot-Path Guardrails

Scope:

- A066-A072 code/tests only.

Gate:

```bash
pytest -q tests/test_a066_meaningful_progress_gate.py tests/test_a067_multi_action_macro_eligibility.py tests/test_a068_execute_memory_read_firewall.py tests/test_a069_hypothesis_falsification.py tests/test_a070_monotonic_terminal_progress.py tests/test_a071_sync_autopilot.py tests/test_a072_harden_solve_context.py
```

### Commit 3: World-Model Foundation

Scope:

- A073-A078 after fixing classifier semantics.

Gate:

```bash
pytest -q tests/test_a073_per_game_world_model.py tests/test_a074_world_model_compiler.py tests/test_a075_aggregate_mechanic_memory.py tests/test_a076_evidence_gated_reasoning_controller.py tests/test_a076_runtime_behavior.py tests/test_a077_world_model_guided_planner.py tests/test_a078_world_model_evaluation_harness.py
```

### Commit 4: Planner/Mechanic-Prior Fixes

Scope:

- A079-A091 after fixing prior transfer, capability-missing semantics, progress snapshot robustness, and label normalization.

Gate:

```bash
pytest -q tests/test_a079_stall_classified_early_stop.py tests/test_a080_world_model_eval_controller_metrics.py tests/test_a081_aggregate_mechanic_memory_transfer.py tests/test_a082_deterministic_cheap_probe_action_path.py tests/test_a083_explicit_early_stop_decision_telemetry.py tests/test_a084_mechanic_memory_transfer_diagnostics.py tests/test_a085_multi_action_no_progress_gate.py tests/test_a086_evidence_backed_planner_predictions.py tests/test_a087_mechanic_prior_recall_signature_quality.py tests/test_a088_compact_smoke_artifact_exports.py tests/test_a089_graph_backed_planner_prediction_edges.py tests/test_a090_mechanic_prior_use_planner_ranking.py tests/test_a091_http_mcp_bridge_degradation.py
```

### Commit 5: Graph/Click/Route Extensions

Scope:

- A092-A110 after world-model foundation is merged.

Gate:

```bash
pytest -q tests/test_a092_terminal_aligned_meaningful_progress.py tests/test_a093_fast_prediction_falsification_action_quarantine.py tests/test_a094_multi_action_churn_exhaustion_decision.py tests/test_a095_deepseek_prompt_compression.py tests/test_a096_terminal_distance_delta_effect_edges.py tests/test_a097_movement_transition_effect_taxonomy.py tests/test_a098_race_safe_early_stop_guardrails.py tests/test_a099_bounded_graph_route_planner.py tests/test_a100_world_model_eval_stream_parity.py tests/test_a106_coordinate_aware_click_action_identity.py tests/test_a107_graph_click_candidate_generator.py tests/test_a108_coordinate_aware_cheap_probe_planner.py tests/test_a109_pattern_correspondence_goal_planner.py tests/test_a110_click_outcome_evaluation_telemetry.py
```

### Archive Commit

Scope:

- Move A092-A095 root research docs to `docs/research/a092-a095/`.
- Remove or quarantine local `mcp_engine/` shim.
- Stage future backlog cards in a clearly labeled backlog staging area.

## Bottom Line

The branch should not be discarded. It has a strong spine.

The safest path is to extract green slices first, fix the planner/prior seam next, and keep local memory shims out of ARC_AGI unless they are explicitly test-only and complete.
