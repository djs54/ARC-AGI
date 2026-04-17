# B130 Plan: Enhanced submission_results logging and observability

**Session Goal**: Design and scope comprehensive logging improvements for diagnostic value and ARC API integration transparency.

## Current State Summary

**What's broken/missing**:
- No submission timestamp → can't correlate runs with system events
- No explicit **request start timestamp** for ARC calls
- No human-friendly “1st response / 2nd response / 3rd response” timeline
- Only summaries of ARC API responses → raw payloads unavailable
- No sequence timeline (mm:ss) → hard to trace causation between steps
- `orchestration_report.phase_owner` all "ARC Harness" → uninformative (though correct by design)
- Frame state captured as hash only → no pixel-level deltas or change vectors
- Missing HTTP metadata → status codes, headers, rate limits lost
- Error context sparse → root cause of API failures not visible

**Where it hurts**:
1. Debugging loop behavior: Can't see exact frame states or API responses that led to same-action repetition
2. Performance analysis: No visibility into where 40+ minutes were spent in live run
3. Learning from failures: Guard escalation logged but decision flow context missing
4. Compliance: No audit trail of raw API payloads

**Why it matters now**:
- Live ARC smoke test revealed agent stuck at wall (steps 12–15) with repeated frame hashes
- Analysis script had to reverse-engineer JSONL to extract trace metadata
- Couldn't easily correlate "step X was at mm:ss Y, API returned Z"

## Solution Blueprint (5-Phase Rollout)

### Phase 1: Timestamps & Metadata
**Goal**: Add ISO8601 submission timestamp + run environment context  
**Scope**: 1 session (1 hour)

**Files to modify**:
- `agents/arc3/runner.py`:
  - [ ] Add `submission_metadata` dict with `created_at` (ISO8601), `submission_id`, `run_duration_seconds`, `environment` (llm_model, memory_backend, arc_api_endpoint)
  - [ ] Pass `start_time` through DurableARCRunner.__init__() and state
  - [ ] Add `timestamp_iso` and `elapsed_mmss` to each sidequests_ledger entry

**Example output**:
```json
{
  "submission_metadata": {
    "created_at": "2026-04-02T14:32:58.234Z",
    "submission_id": "sub_20260402_143258_arc_eval_001",
    "run_duration_seconds": 2430.3,
    "environment": {
      "llm_model": "llama3.1:8b",
      "llm_endpoint": "http://localhost:11434/v1",
      "memory_backend": "kuzu_0.11.3",
      "arc_api_endpoint": "three.arcprize.org"
    }
  },
  "sidequests_ledger": [
    {
      "step": 1,
      "timestamp_iso": "2026-04-02T14:33:05.234Z",
      "elapsed_mmss": "00:07",
      "phase": "bootstrap",
      ...
    }
  ]
}
```

**Tests**:
- `test_submission_metadata_iso8601()` — verify format and timezone
- `test_elapsed_mmss_monotonic()` — verify increasing mm:ss values

---

### Phase 2: Frame Delta Analysis
**Goal**: Capture pixel-level changes between consecutive frames  
**Scope**: 1 session (1.5 hours)

**Files to modify**:
- `agents/arc3/runner.py`:
  - [ ] Add `_analyze_frame_delta(prev_frame, curr_frame) -> dict` helper
  - [ ] Return `frame_analysis` with: `pixels_changed`, `bounding_box_change`, `movement_detected`, `new_colors`, `colors_removed`, `analysis` (human-readable insight)
  - [ ] Integrate into solve_phase_summary on each ARC API response

**Example output**:
```json
{
  "step": 12,
  "frame_analysis": {
    "pixels_changed": 0,
    "movement_detected": false,
    "bounding_box_change": null,
    "new_colors_introduced": [],
    "colors_removed": [],
    "analysis": "Frame unchanged from previous step — player at wall, ACTION1 (up) blocked"
  }
}
```

**Tests**:
- `test_frame_delta_no_change()` — same frame twice
- `test_frame_delta_player_movement()` — player moved, rest of grid unchanged
- `test_frame_delta_color_swap()` — pixel changed from color X to Y
- `test_frame_delta_bounding_box()` — new object appeared

---

### Phase 3: Raw ARC API Trace Capture
**Goal**: Capture HTTP method, status, headers, raw response payloads, and a CloudWatch-style event stream  
**Scope**: 1.5 sessions (2 hours)

**Required output path per call**:
- `submission_results[*].sidequests_ledger[*].arc_api_io.request`
- `submission_results[*].sidequests_ledger[*].arc_api_io.response`
- `submission_results[*].sidequests_ledger[*].arc_api_io.call_seq` (strictly increasing)
- `submission_results[*].arc_event_timeline[*]`

**Files to modify**:
- `benchmarks/arc3/adapter.py`:
  - [ ] Intercept POST /api/cmd/{ACTION_ID} calls in `execute_action()` or `_post()`
  - [ ] Log `http_method`, `http_endpoint`, `http_status`, `request_size_bytes`, `response_size_bytes`, `response_headers`
  - [ ] Build `arc_api_io.request` + `arc_api_io.response` for each ARC call
  - [ ] Capture `request_started_iso`, `response_received_iso`, and `duration_ms`
  - [ ] Increment and attach `arc_api_io.call_seq` for each ARC call
  - [ ] Optionally capture raw `raw_frame_response` (full FrameResponse JSON)
  - [ ] Add config flag `CAPTURE_RAW_ARC_TRACES=false` (env var toggle)
  - [ ] Truncate if response > 10MB; warn and skip capture

- `agents/arc3/runner.py`:
  - [ ] Pass adapter trace context through action dispatch logic
  - [ ] Add required `arc_api_io` field to sidequests_ledger entry for each ARC call
  - [ ] Build top-level `arc_event_timeline` with `event_seq`, `request_started`, and `response_received` entries
  - [ ] Add `arc_api_trace` field to sidequests_ledger entry for each action

**Example output**:
```json
{
  "step": 10,
  "phase": "act",
  "call_type": "arc_api_action",
  "arc_api_trace": {
    "http_method": "POST",
    "http_endpoint": "/api/cmd/ACTION1",
    "http_status": 200,
    "request_size_bytes": 156,
    "response_size_bytes": 4821,
    "response_headers": {
      "content-type": "application/json",
      "x-ratelimit-remaining": "499",
      "x-ratelimit-reset": "2026-04-02T14:36:00Z"
    },
    "raw_frame_response": {
      "frame": [[0, 1, 2, ...], ...],
      "state": "NOT_FINISHED",
      "available_actions": [1, 2, 3],
      "score": 0,
      "reward": 0.0,
      "time_left": 3600
    }
  }
}
```

**Tests**:
- `test_arc_api_trace_200_ok()` — mock 200 response
- `test_arc_api_trace_headers()` — verify rate-limit headers captured
- `test_arc_api_trace_truncation()` — large response truncated with warning
- `test_raw_traces_off_by_default()` — env var controls capture
- `test_arc_api_io_per_call_present()` — every ARC call has request+response object
- `test_arc_api_io_call_seq_monotonic()` — call sequence strictly increases
- `test_arc_event_timeline_first_second_third_response()` — verify first/second/third server responses are visible in order
- `test_request_started_timestamp_present()` — verify every ARC call logs a start timestamp before the response

---

### Phase 4: Decision Flow & Guard Escalation History
**Goal**: Replace generic `phase_owner` with actual decision flow; surface guard state transitions  
**Scope**: 1.5 sessions (2 hours)

**Files to modify**:
- `agents/arc3/runner.py`:
  - [ ] Replace `phase_owner` dict with `decision_flow` dict showing who *proposed* vs. *executed* each phase
  - [ ] Add `guard_escalations` list to orchestration_report with all `{step, reason, guard_state}` tuples
  - [ ] Link guard escalations to corresponding sidequests_ledger entries

- `agents/arc3/orchestrator.py`:
  - [ ] Log guard escalation events with reason (`loop_detected`, `distance_not_shrinking`, etc.)
  - [ ] Pass escalation history to runner

**Example output**:
```json
{
  "orchestration_report": {
    "decision_flow": {
      "bootstrap": {
        "proposed_by": ["Arc Agent", "SideQuests"],
        "executed_by": "Arc Harness",
        "policy_applied": "sequential entity discovery"
      },
      "solve": {
        "proposed_by": ["Arc Agent"],
        "executed_by": "Arc Harness",
        "policy_applied": "chunk enforcement with directional guard",
        "guard_escalations": [
          {"step": 12, "reason": "loop detected (repeated frame hash)", "guard_state": "warned"},
          {"step": 13, "reason": "distance not shrinking", "guard_state": "warned"},
          {"step": 14, "reason": "continued guard escalation", "guard_state": "warned"}
        ]
      }
    },
    "violations": [],
    "status": "ok"
  }
}
```

**Tests**:
- `test_decision_flow_bootstrap()` — verify proposed_by list
- `test_decision_flow_solve()` — verify executor vs. proposer
- `test_guard_escalation_history()` — verify guard state transitions logged
- `test_guard_escalation_reasons()` — verify reason field set correctly

---

### Phase 5: Error Context & Diagnostics
**Goal**: Capture root cause of API failures and network issues  
**Scope**: 1 session (1.5 hours)

**Files to modify**:
- `benchmarks/arc3/adapter.py`:
  - [ ] Wrap HTTP calls in try/except; capture error_type (ConnError, Timeout, InvalidAction, etc.)
  - [ ] Log `error_code`, `error_message`, `retry_count`, `retry_backoff_ms`, `wall_time_elapsed_ms`
  - [ ] Add diagnostics field (e.g., "Network latency spike; server may be rate-limiting")

- `agents/arc3/runner.py`:
  - [ ] Add `api_failure_trace` field to sidequests_ledger on error
  - [ ] Preserve failure entry in ledger even if action not executed

**Example output**:
```json
{
  "step": 5,
  "phase": "act",
  "api_failure_trace": {
    "error_type": "connection_timeout",
    "http_status": null,
    "error_code": "ECONN_TIMEOUT",
    "error_message": "POST /api/cmd/ACTION2 timed out after 30s",
    "retry_count": 0,
    "wall_time_elapsed_ms": 30000,
    "diagnostics": "Network latency spike; server may be rate-limiting or overloaded. Consider backoff."
  }
}
```

**Tests**:
- `test_api_failure_connection_reset()` — mock connection reset
- `test_api_failure_timeout()` — mock 30s timeout
- `test_api_failure_rate_limit_429()` — mock rate limit
- `test_api_failure_invalid_action()` — mock invalid action error

---

## Backwards Compatibility & Migration

✅ **Non-breaking**: New fields additive; old fields preserved  
✅ **Optional**: `arc_api_trace` and frame pixel data off by default  
✅ **Parser resilience**: Existing scripts can ignore new fields  

**Migration path**:
1. Phase 1: Add timestamps (non-breaking)
2. Phase 2-5: Mark new fields as optional in schema
3. Update `submission_results_analyzer` scripts to consume new fields optionally

---

## Testing Checklist

**Unit Tests** (Phase 1–5):
- [ ] `test_metadata_iso8601_format()`
- [ ] `test_elapsed_mmss_calculation()`
- [ ] `test_frame_delta_no_change()`
- [ ] `test_frame_delta_movement()`
- [ ] `test_arc_api_trace_200()`
- [ ] `test_arc_api_trace_headers()`
- [ ] `test_guard_escalation_history()`
- [ ] `test_api_failure_timeout()`

**Integration Tests** (Phase 1–5):
- [ ] `test_live_submission_with_timestamps()`
- [ ] `test_live_frame_delta_trace()`
- [ ] `test_live_guard_escalation_visible()`
- [ ] `test_live_error_context_captured()`

**Smoke Tests**:
- [ ] Run mock ARC puzzle → Verify all new fields present
- [ ] Run live ARC puzzle → Verify timestamps increase, frame deltas computed, guard escalations logged

---

## Priority & Timeline

**Priority**: P1 — High diagnostic and compliance value  
**Estimated effort**: 5 sessions (~6–8 hours) across 2–3 days  
**Blockers**: None (orthogonal to B129 regression fixes)  
**Dependencies**: None (can start immediately after B129 commits)

---

## Implementation Notes

1. **Timestamp clock**: Use `time.time()` at run start, then `elapsed_ms` for each entry for accuracy
2. **mm:ss formatting**: `f"{int(elapsed_ms // 60000)}:{int((elapsed_ms % 60000) // 1000):02d}"`
3. **Frame delta efficiency**: Don't store full grids; just bounding box + changed pixels
4. **API trace size**: Warn if `> 10MB` total, truncate raw payload if `> 5MB` single entry
5. **Guard escalation mapping**: Cross-reference step numbers in decision_flow with sidequests_ledger

---

## Success Criteria

✅ Submission results include ISO8601 timestamp  
✅ Every ledger entry has mm:ss elapsed marker and ISO timestamp  
✅ Frame deltas computed and visible in solve_phase_summary  
✅ Raw ARC API traces captured (opt-in, off by default)  
✅ Decision flow shows who proposed vs. executed  
✅ Guard escalation history visible with reasons  
✅ API errors surface root cause and diagnostics  
✅ All tests pass (unit + integration + live smoke)  
✅ JSON size doesn't exceed 200MB (raw traces off) or 500MB (raw traces on)  

