# Plan: A-111 — Complete Name-Change Overhaul Across Smoke Artifacts

## Card metadata

- **Card:** A111
- **Priority:** P1
- **Layer:** evaluation/harness
- **Depends on:** A078, A088, A100, A110
- **Intended executor:** GPT-5.3 Codex subagent

## Summary

Complete the smoke-artifact name-change overhaul without changing puzzle-solving policy. The work is to centralize canonical names and apply them at emission boundaries so every output document describes the same run with the same vocabulary.

The subagent should treat this as a telemetry-contract cleanup, not a solver-tuning card.

## Implementation approach

1. Inspect the current emitters.
   - Read `ARCHITECTURE.md` for the current ARC-AGI-3 naming intent.
   - Trace where `phase`, `task_id`, `run_review`, failure fields, and click-candidate fields are emitted into:
     - `submission_results_single.json`
     - `submission_results_arcServer.json`
     - `submission_results_single.live.jsonl`
     - `submission_results_single.world_model.live.jsonl`
     - `agent_execution_trace.json`
     - `master_timeline.json`

2. Add a small canonical naming helper.
   - Prefer a focused module such as `agents/arc3/trace_names.py`.
   - Keep it dependency-light and runtime-safe.
   - Do not import `mcp_engine.*`, `campy.*`, or `sidequests.*`.

3. Define the canonical top-level phase vocabulary.
   - Use these primary phase names unless `ARCHITECTURE.md` already defines a stricter current set:
     - `setup`
     - `perceive`
     - `model`
     - `plan`
     - `act`
     - `evaluate`
     - `replan`
     - `summary`
   - Normalize known legacy aliases at artifact boundaries:
     - `discover` -> `model`
     - `hypothesize` -> `model` when the record is goal/archetype/mechanic inference
     - `hypothesize` -> `plan` when the record is strategy/action-family selection
     - `route` -> `plan`
     - `execute` -> `act`
     - `unknown` -> `setup` or `summary` based on the record context
   - Preserve the original value only in `legacy_phase` when useful for compatibility.

4. Stabilize task identity.
   - Preserve the ARC runner task ID as `task_id`, for example `arc_eval_001`.
   - Move UUID-like SideQuests graph IDs, quest IDs, session IDs, or run IDs into explicit fields such as `task_graph_id`, `quest_id`, `sidequest_task_id`, `run_id`, or `arc_session_id`.
   - Add regression coverage proving `task_id` does not become a UUID in trace, timeline, live streams, or final JSON.

5. Normalize action override reason labels.
   - Replace stale action-specific labels like `replan_forced_action6_probe` with neutral names such as `replan_forced_probe`, unless the emitted action really is `ACTION6`.
   - Where possible, include `requested_action`, `selected_action`, `override_action`, and `override_reason` as separate structured fields.

6. Normalize run-review links and artifact references.
   - Choose one primary `test_results_url`, normally the final compact JSON result or a configured primary output path.
   - Add an `artifact_urls` or `artifact_paths` map when multiple documents are referenced.
   - Ensure all artifacts either share the same primary URL or clearly identify themselves as secondary artifacts without contradictory primary links.

7. Harden crash/failure payloads.
   - Add or normalize fields:
     - `failure_class`
     - `failure_reason`
     - `exception_type`
     - `exception_message`
     - `trace_artifact`
   - Ensure final `orchestration_status` is not `ok` when `failure_class=crash`.
   - Do not swallow the existing compact summary fields.

8. Align click-candidate stream fields.
   - Ensure selected click-candidate ID, coordinate, rank, source, predicted effect, and provenance are emitted consistently in main live JSONL and world-model live JSONL.
   - Fix rank mismatches such as main stream rank `0` becoming world-model stream rank `-1`.
   - Keep null-click/click-supported evaluation conservative: a frame/config change alone should not be overreported as terminally useful support.

9. Keep the MCP seam and import boundary green.
   - Runtime production code must not import `mcp_engine.*`, `campy.*`, or `sidequests.*`.
   - `benchmarks/arc3/` may keep its existing offline packaging exemptions.

## Concrete file additions/edits

- Add `agents/arc3/trace_names.py` or equivalent:
  - `CANONICAL_PHASES`
  - `PHASE_ALIASES`
  - `canonical_phase(value, *, context=None)`
  - `normalize_task_identity(payload, canonical_task_id)`
  - `normalize_failure_payload(error_or_result)`
  - `normalize_run_review(primary_artifact_path, artifact_paths, game_metadata, result)`

- Update emission sites in:
  - `agents/arc3/orchestrator.py`
  - `agents/arc3/runner.py`
  - `benchmarks/arc3/world_model_eval.py`
  - `run_single_puzzle.py`
  - `benchmarks/arc3/adapter.py` if ARC-server packaging emits divergent naming or link fields

- Add `tests/test_a111_name_change_overhaul.py` with focused regression fixtures.

## API/interface changes

- No external API change is required.
- Artifact schema changes are additive where compatibility matters.
- Primary artifact fields should use canonical names. Legacy values may be retained in explicitly named compatibility fields.

## Tests to add or run

```bash
.venv/bin/python -m pytest -q tests/test_a111_name_change_overhaul.py
.venv/bin/python -m pytest -q \
  tests/test_a078_world_model_evaluation_harness.py \
  tests/test_a088_compact_smoke_artifact_exports.py \
  tests/test_a100_world_model_eval_stream_parity_route_decisions.py \
  tests/test_a110_click_outcome_evaluation_telemetry.py \
  tests/test_import_boundary.py
make test-a
```

If one of the adjacent test filenames differs in the current checkout, locate the matching A078/A088/A100/A110 test with `rg --files tests | rg 'a078|a088|a100|a110'` and run the equivalent target.

## Regression fixtures to include

- A mixed artifact bundle where `task_id` is `arc_eval_001` but a SideQuests UUID is present. The normalized result must keep `task_id=arc_eval_001`.
- Phase rows containing `discover`, `hypothesize`, `route`, `execute`, and `unknown`. The primary `phase` values must normalize to the canonical vocabulary.
- A guard override from `ACTION5` to `ACTION1` with a stale `replan_forced_action6_probe` reason. The normalized reason must not mention `ACTION6`.
- A crash result with an exception. The final payload must include failure details and must not report `orchestration_status=ok`.
- Matching selected click-candidate rows in main live and world-model streams. Rank and candidate identity must match.

## Assumptions/defaults

- Do not tune planner policy, goal induction, graph extraction, or click candidate ranking in this card.
- Do not remove detailed trace fields unless A088 compact-output rules already require them to live in a dedicated artifact.
- Prefer normalization at the boundary where artifacts are emitted over broad internal renames that increase regression risk.
- Keep comments sparse and use them only around non-obvious compatibility mapping.
