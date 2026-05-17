# A092-A095 Implementation Checklist

## Quick Reference: Methods to Add/Modify

### A092 — Terminal Alignment (Priority P0)

**New Methods:**

| File | Signature | Purpose |
|------|-----------|---------|
| `grid_analysis.py` | `compute_goal_distance(observation: Obs, goal_model: GoalModel) -> float` | Distance from current terminal_score to win_score |
| `world_model_compiler.py` | `classify_effect_alignment(effect_node: Node, prev_obs: Obs, curr_obs: Obs) -> str` | Return "terminal_aligned" \| "object_local" \| "delayed_candidate" |
| `world_model.py` | **property on Effect node** | Add `alignment_class: str` field when recording effects |

**Modified Methods:**

| File | Method | Change |
|------|--------|--------|
| `world_model_compiler.py` | `compile(world_model, goal_model)` | Accept goal_model param; compute alignments |
| `world_model_planner.py` | `_rank_candidates_by_evidence_backing()` | Use alignment in tier scoring (terminal_aligned = tier -1) |
| `orchestrator.py` | `_build_mechanic_recall_signature()` | Include goal distance delta in signature |

**Telemetry to Add:**

- `planner_candidate_alignment_tier` (for selected candidate)
- `terminal_distance_current` / `terminal_distance_previous` (per step)
- `meaningful_progress_is_terminal_aligned` (boolean)

---

### A093 — Fast Falsification & Quarantine (Priority P0)

**New Methods:**

| File | Signature | Purpose |
|------|-----------|---------|
| `world_model.py` | `record_prediction_miss(pred_node_id: str, actual_effect_node_id: str, confidence: float) -> str` | Create CONTRADICTED_BY edge, trigger quarantine if count > 1 |
| `world_model.py` | `quarantine_action(action_id: str, ttl: int = 5)` | Add to quarantine dict with expiry step |
| `world_model.py` | `is_action_quarantined(action_id: str) -> bool` | Check if in quarantine dict and TTL not expired |
| `world_model.py` | `get_quarantine_state() -> Dict[str, int]` | Return {action_id: ttl_expires_at_step} |
| `world_model_planner.py` | `_compute_prior_compatibility_score(prior: Dict, world_model: WM, action_id: str) -> float` | Boost on match, reduce on contradictions |

**Modified Methods:**

| File | Method | Change |
|------|--------|--------|
| `world_model_planner.py` | `_rank_candidates_by_evidence_backing()` | Check quarantine; set tier 3 if quarantined |
| `world_model_planner.py` | `select_next_candidate()` | Pass `budget_state.quarantined_actions` to ranker |
| `orchestrator.py` | `_execute_action()` (or wherever action result observed) | Call `world_model.record_prediction_miss()` if prediction ≠ observation |
| `orchestrator.py` | `_decide_next_action()` | Extract quarantine state before planner selection |

**Telemetry to Add:**

- `selected_candidate_prediction_missed` (boolean)
- `selected_candidate_prior_compatibility_score` (0.0-1.0)
- `action_quarantine_count` (how many actions in quarantine)
- `action_quarantine_list` (string list)

---

### A094 — Multi-Action Churn Exhaustion Decision (Priority P0)

**New Methods:**

| File | Signature | Purpose |
|------|-----------|---------|
| `world_model.py` | `check_multi_action_exhaustion(available_actions: List[str], evidence_threshold: int = 2, include_delayed_check: bool = True) -> bool` | True if all actions have evidence + no terminal progress + no delayed hope |
| `world_model.py` | `has_terminal_aligned_progress(recent_window: int = 5) -> bool` | Check for terminal-aligned effects in last N steps |

**Modified Methods:**

| File | Method | Change |
|------|--------|--------|
| `reasoning_controller.py` | `decide()` | Add exhaustion check after A093 logic; emit `ReasoningMode.MULTI_ACTION_STRATEGY_EXHAUSTED` if triggered |
| `runner.py` | `_emit_world_model_decision_snapshot()` | Capture `world_model_decision` field; emit as JSONL row |
| `orchestrator.py` | `_decide_next_action()` | Handle exhaustion decision (early stop or reclassification) |

**Decision Type:**
- New enum value: `ReasoningMode.MULTI_ACTION_STRATEGY_EXHAUSTED` (already exists, use it)
- New ReasoningDecision field: `world_model_decision = "multi_action_churn_exhausted"`

**Telemetry to Add:**

- `world_model_decision` (string: "multi_action_churn_exhausted" or null)
- `multi_action_exhaustion_check_result` (enum: exhausted, not_yet_exhausted, delayed_guard_active)
- `exhaustion_evidence_path` (which actions+effects led to decision)

---

### A095 — Prompt Compression (Priority P1)

**New Methods:**

| File | Signature | Purpose |
|------|-----------|---------|
| `world_model.py` | `get_delta_since(last_render_step: int) -> Dict[str, Any]` | Return new nodes, edges, hypotheses, contradictions |
| `prompts.py` | (constant) | `REQUIRED_BLOCKS = ["SYSTEM", "CURRENT_STATE", "AVAILABLE_ACTIONS", "PLANNER_PROPOSALS"]` |
| `prompts.py` | (new template) | `WORLD_MODEL_DELTA_TEMPLATE = "..."`  |

**Modified Methods:**

| File | Method | Change |
|------|--------|--------|
| `orchestrator.py` | `_build_prompt()` (or wherever PromptPacket is constructed) | On step > 1: call `world_model.get_delta_since()`; build compressed packet with delta block + required blocks |
| `orchestrator.py` | `ARCOrchestrator.__init__()` | Add `_last_packet_render_step: int = 0` field |
| `runner.py` | (telemetry) | Track `prompt_tokens_compressed` vs `prompt_tokens_full` estimates |

**Telemetry to Add:**

- `prompt_compression_enabled` (boolean, true on step > 1)
- `prompt_tokens_estimated_full` (what it would be uncompressed)
- `prompt_tokens_estimated_compressed` (actual rendered tokens)
- `prompt_compression_ratio` (compressed / full)
- `prompt_blocks_rendered` (list of block types)

---

## Dependency Flow

```
A092 (terminal alignment)
  ↓ uses goal distance to classify effects
  ↓
A093 (falsification + quarantine)
  ↓ reads alignment to suppress object_local from exploit
  ↓ records prediction misses as graph contradictions
  ↓
A094 (churn exhaustion decision)
  ↓ reads quarantine + terminal_aligned progress
  ↓ emits explicit decision
  ↓
A095 (prompt compression)
  ↓ uses delta from settled world model
  ↓ requires decision to know when to compress
```

**Recommended implementation order**: A092 → A093 → A094 → A095

---

## Test File Template

Each test should follow pattern (example A092):

```python
# tests/test_a092_terminal_aligned_meaningful_progress.py

class TestTerminalAlignedMeaningfulProgress:
    
    def test_terminal_regressing_with_object_progress_is_local_only(self):
        """Local object progress without terminal improvement should be classified as object_local."""
        # Setup: action causes object movement but terminal distance increases (bad)
        # Verify: effect.alignment_class == "object_local"
        
    def test_terminal_improving_with_object_progress_is_aligned(self):
        """Object progress with improving terminal should be terminal_aligned."""
        # Setup: action causes object movement AND terminal distance decreases (good)
        # Verify: effect.alignment_class == "terminal_aligned"
        
    def test_delayed_effect_candidate_identified(self):
        """Object progress followed by later terminal improvement should be delayed_candidate."""
        # Setup: step N has object progress, step N+2 has terminal progress
        # Verify: step N effect has delayed_candidate classification or explicit delayed edge
        
    def test_exploit_ranking_prefers_terminal_aligned(self):
        """Terminal-aligned exploit should rank higher than object_local even with same confidence."""
        # Setup: 2 candidates with same confidence, one terminal_aligned, one object_local
        # Verify: terminal_aligned selected (tier -1 vs tier 2)
```

---

## Config Changes Needed

**Add to config.yaml** (or pass via defaults):

```yaml
reasoning_gate:
  falsification_quarantine_confidence_threshold: 0.75
  falsification_quarantine_count_threshold: 2
  falsification_quarantine_ttl_steps: 5
  multi_action_exhaustion_evidence_threshold: 2
  multi_action_exhaustion_include_delayed_check: true
  prompt_compression_enabled: true
  prompt_compression_start_step: 2  # Full packet on step 1, compressed on step 2+
  prompt_compression_target_ratio: 0.4  # Goal: 40% of original tokens
```

---

## Acceptance Criteria Checklist

### A092 Acceptance
- [ ] Object progress with regressing terminal distance is classified as `object_local`
- [ ] Terminal-aligned effects create causal edge in graph
- [ ] Exploit ranking uses alignment tier
- [ ] Tests pass for flat, regressing, oscillating, delayed, improving cases
- [ ] Telemetry captures alignment classification

### A093 Acceptance
- [ ] 2 consecutive high-confidence prediction misses quarantine action
- [ ] Quarantine TTL expires, action becomes exploitable again
- [ ] Planner ranking suppresses quarantined actions from exploit tier
- [ ] Low-confidence misses do not trigger quarantine
- [ ] Fallback works if all legal actions quarantined (use lowest-tier action)
- [ ] Telemetry exposes quarantine state

### A094 Acceptance
- [ ] All legal actions with ≥2 effects + no terminal progress → `multi_action_churn_exhausted`
- [ ] Decision emitted as JSONL row
- [ ] Runner stops or changes strategy instead of continuing cheap probes
- [ ] Delayed-effect guard prevents premature exhaustion
- [ ] Tests pass for exhausted, not-yet-exhausted, delayed-guard, reset branches
- [ ] Telemetry captures decision

### A095 Acceptance
- [ ] Full prompt rendered on step 1
- [ ] Delta block replaces STATE/ENTITY/MEMORY/PLAN on step 2+
- [ ] Required blocks always present (legal actions, goal, observation, planner proposals)
- [ ] Estimated token count drops 60%+ per step
- [ ] DeepSeek/Ollama smoke input tokens per step materially reduced
- [ ] Tests verify compression ratio and required content retention

---

## Files Modified Summary

```
agents/arc3/
  ├─ world_model.py
  │   ├─ add: record_prediction_miss()
  │   ├─ add: quarantine_action() / is_action_quarantined() / get_quarantine_state()
  │   ├─ add: check_multi_action_exhaustion()
  │   ├─ add: has_terminal_aligned_progress()
  │   ├─ add: get_delta_since()
  │   └─ modify: record_effect() [add alignment_class property]
  │
  ├─ world_model_planner.py
  │   ├─ add: _compute_prior_compatibility_score()
  │   ├─ modify: _rank_candidates_by_evidence_backing() [use alignment, check quarantine]
  │   ├─ modify: select_next_candidate() [pass quarantine state]
  │   └─ modify: _generate_prediction_for_action() [A089 follow-up]
  │
  ├─ world_model_compiler.py
  │   ├─ add: classify_effect_alignment()
  │   └─ modify: compile() [accept goal_model, compute alignments]
  │
  ├─ reasoning_controller.py
  │   ├─ add: _check_delayed_effect_guard()
  │   └─ modify: decide() [add exhaustion check, emit decision]
  │
  ├─ orchestrator.py
  │   ├─ add: _last_packet_render_step field
  │   ├─ modify: _execute_action() [record prediction miss]
  │   ├─ modify: _decide_next_action() [handle exhaustion decision]
  │   └─ modify: _build_prompt() [call get_delta_since(), build compressed packet]
  │
  ├─ grid_analysis.py
  │   └─ add: compute_goal_distance()
  │
  └─ prompts.py
      ├─ add: REQUIRED_BLOCKS
      ├─ add: WORLD_MODEL_DELTA_TEMPLATE
      └─ modify: PromptPacket.render() [support delta mode]

benchmarks/arc3/
  └─ world_model_eval.py
      └─ modify: build_step_row() [capture new telemetry fields]

tests/
  ├─ test_a092_terminal_aligned_meaningful_progress.py [NEW]
  ├─ test_a093_fast_prediction_falsification_action_quarantine.py [NEW]
  ├─ test_a094_multi_action_churn_exhaustion_decision.py [NEW]
  └─ test_a095_deepseek_prompt_compression.py [NEW]

(optional)
├─ runner.py [modify: _emit_world_model_decision_snapshot()]
├─ config.yaml [add reasoning_gate section]
└─ backlog/plans/ [link to detailed plans if needed]
```
