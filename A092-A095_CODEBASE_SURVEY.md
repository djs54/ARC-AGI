# ARC-AGI A092-A095 Codebase Survey

**Date**: May 6, 2026  
**Scope**: Quick structure assessment for implementing terminal-aligned progress, fast falsification, churn decision, and prompt compression

## Executive Summary

**Current state**: Graph infrastructure (A073-A090) is ~60% ready for A092-A095. Prediction generation and falsification conditions exist but are not stored in the graph. Terminal-distance tracking is absent. Decision emission framework exists but `multi_action_churn_exhausted` decision not yet emitted. Prompt compression not implemented.

**Critical blockers for A092**:
- No goal distance computation or terminal-alignment classification
- Goal model not exposed to graph compiler
- Effects lack alignment metadata

**Critical blockers for A093**:
- Prediction misses not recorded as graph edges
- Quarantine state not persisted or readable by planner
- High-confidence threshold for quarantine triggers not defined

**Critical blockers for A094**:
- Multi-action exhaustion check method missing
- Delayed-effect guard logic not coordinated between controller and reasoning
- Exhaustion decision type defined but not emitted

**Critical blockers for A095**:
- Delta computation not implemented
- "Required blocks" list not defined (would prevent loss of legal actions/state)
- Compression heuristics not calibrated

---

## A. Terminal/Goal Distance Tracking

### Current State

**Missing entirely**:
- No `goal_distance` or `terminal_alignment` fields in graph
- No computation of distance = `current_terminal_score` - `win_score`
- Observations record reward/terminal_score but do not track **progress delta**

**Available**:
- `world_model.record_state(step, frame_hash)` records observation with score
- `Observation` node properties: `reward`, `terminal_score`
- `grid_analysis.py` exists for grid-level analysis

### Integration Points for A092

1. **New method in `grid_analysis.py`**:
   ```python
   def compute_goal_distance(observation, goal_model) -> float:
       """Distance from current state to win condition."""
       return goal_model.win_score - observation.terminal_score
   ```

2. **New classification in `world_model_compiler.py`**:
   ```python
   def classify_effect_alignment(effect, observation_delta, goal_model) -> str:
       """Return: 'terminal_aligned' | 'object_local' | 'delayed_candidate'"""
       goal_distance_improved = observation_delta.terminal_distance < prev_terminal_distance
       if effect == "object_progress" and not goal_distance_improved:
           return "object_local"
       # ... other cases
   ```

3. **Property added to graph Effect nodes**:
   - `alignment_class: str` — transient, computed during compile step

4. **Planner ranking uses alignment**:
   - Terminal-aligned exploit candidates ranked tier -1 (above predictions+falsification)
   - Exploit tier for object_local effects reduced when terminal regressing

---

## B. Prediction Storage & Falsification

### Current State

**Predictions exist but ephemeral**:
- Generated in `world_model_planner._generate_prediction_for_action()` (line 116)
- Stored in `PlanCandidate.predicted_observation` dict
- Not persisted to graph
- Discarded after action selection

**Falsification condition exists**:
- Generated in `world_model_planner._generate_falsification_condition_for_action()` (line 180)
- Rules: terminal progress → "terminal distance improves"; object progress → "objects move"; etc.
- String-based (not structured data)
- Used for ranking but not for contradiction tracking

**Observation matching happens in orchestrator**:
- After action: observation compared to prediction (manually)
- Miss → logged but not recorded as graph evidence

### Integration Points for A093

1. **Graph prediction node**:
   ```python
   # In world_model.py
   def record_prediction(action_id, effect_class, confidence, evidence_path_ids) -> str:
       """Create Prediction node, return node_id."""
       node_id = f"pred-{action_id}-{step}-{uuid}"
       self.add_node(node_id, "Prediction", {
           "action_id": action_id,
           "effect_class": effect_class,
           "confidence": confidence,
           "evidence_path_ids": evidence_path_ids
       })
       return node_id
   ```

2. **Prediction miss edge**:
   ```python
   def record_prediction_miss(prediction_node_id, actual_effect_node_id) -> str:
       """Create edge: Prediction → CONTRADICTED_BY → Effect"""
       self.add_edge(prediction_node_id, "CONTRADICTED_BY", actual_effect_node_id, {
           "miss_confidence": prediction.confidence
       })
       # Trigger quarantine if count > threshold
   ```

3. **Quarantine state**:
   ```python
   def quarantine_action(action_id: str, ttl: int = 5):
       """Temporarily suppress from exploit selection."""
       self._quarantined_actions[action_id] = current_step + ttl
   
   def is_action_quarantined(action_id: str) -> bool:
       return action_id in self._quarantined_actions
   ```

4. **Planner reads quarantine**:
   - In `_rank_candidates_by_evidence_backing()`: set evidence_tier = 3 (below everything) if quarantined

---

## C. Decision Emission & World-Model Snapshots

### Current State

**Decisions exist**:
- `ReasoningMode` enum: CHEAP_EXECUTE, LLM_REASON, EARLY_STOP, etc. (line ~10 in reasoning_controller.py)
- `ReasoningDecision` dataclass: mode, trigger, skipped_reason, etc.
- Multi-action churn modes: MULTI_ACTION_CHURN_PROBE, MULTI_ACTION_RECLASSIFY, MULTI_ACTION_STRATEGY_EXHAUSTED

**Decision emission**:
- Orchestrator calls `reasoning_controller.decide()` ~line 2849
- Decision used to select planner mode, gating reasoning
- **Missing**: MULTI_ACTION_STRATEGY_EXHAUSTED never actually emitted

**Snapshot capture**:
- `runner.py:_emit_world_model_decision_snapshot()` @ line 3203
- Writes to JSONL telemetry
- Fields: reasoning_mode, trigger, stall_policy, multi_action_churn_detected, world_model_decision (rarely populated)

### Integration Points for A094

1. **Exhaustion check in `world_model.py`**:
   ```python
   def check_multi_action_exhaustion(available_actions: List[str], 
                                     evidence_threshold: int = 2) -> bool:
       """True if all actions have ≥evidence_threshold effects + no terminal progress."""
       for action_id in available_actions:
           effect_count = len(self.get_action_effect_table(action_id=action_id))
           if effect_count < evidence_threshold:
               return False
       return not self.has_terminal_aligned_progress()
   ```

2. **Decision in `reasoning_controller.py`**:
   ```python
   # In decide() after A093 quarantine logic:
   if world_model.check_multi_action_exhaustion(available_actions):
       if not self._delayed_effect_guard_applies(mechanic_priors):
           decision = ReasoningDecision(
               mode=ReasoningMode.MULTI_ACTION_STRATEGY_EXHAUSTED,
               world_model_decision="multi_action_churn_exhausted"
           )
   ```

3. **Snapshot telemetry**:
   - Add fields: action_id, effect_class, contradiction_count, quarantine_state
   - Write JSONL row with decision

---

## D. Prompt Generation & Compression

### Current State

**Prompt structure** (orchestrator.py @ line ~130):
```python
class PromptPacket:
    blocks: List[ContentBlock]
    
# Render order: SYSTEM, TRAINING_EXAMPLES, SOLVED_LEVELS, PRIOR_INSIGHTS,
# GRID_ANALYSIS, STATE, ENTITY_CONTEXT, MEMORY, PLAN, INSTRUCTION, ...
```

**Block budget** (prompts.py):
- MAX_PROMPT_LESSONS = 1
- MAX_PROMPT_HISTORY = 2
- MAX_PROMPT_HYPOTHESES = 1
- MAX_PROMPT_ACTIONS = 4

**Current behavior**:
- Full packet rendered every step
- ~13k tokens per step reported (391k / 30 steps for DeepSeek)
- STATE, ENTITY_CONTEXT, MEMORY, PLAN blocks repeated with minimal delta

**Prompt rendering**:
- `PromptPacket.render()` @ line 130 in orchestrator.py
- Concatenates blocks with headers
- No delta mode

### Integration Points for A095

1. **Delta computation in `world_model.py`**:
   ```python
   def get_delta_since(self, last_render_step: int) -> Dict[str, Any]:
       """Nodes, edges, hypotheses, contradictions changed since step."""
       return {
           "new_nodes": [n for n in self.nodes.values() if n.props["step"] > last_render_step],
           "new_edges": [e for e in self.edges if e.props.get("step", 0) > last_render_step],
           "active_contradictions": [c for c in contradictions if not resolved],
           "changed_hypotheses": [h for h in hypotheses if h.props["step"] > last_render_step]
       }
   ```

2. **Compression block in `prompts.py`**:
   ```python
   WORLD_MODEL_DELTA_TEMPLATE = """
   === WORLD MODEL UPDATE (compressed) ===
   Graph: +{node_count} nodes, +{edge_count} edges (step {last_step} → {current_step})
   New contradictions: {contradiction_summary}
   Active quarantines: {quarantine_list}
   Next experiment: {planner_proposal}
   """
   
   REQUIRED_BLOCKS = ["SYSTEM", "CURRENT_STATE", "AVAILABLE_ACTIONS", "PLANNER_PROPOSALS"]
   ```

3. **Orchestrator compression mode**:
   ```python
   # In ARCOrchestrator._build_prompt():
   if self._current_step > 1:
       delta = self.world_model.get_delta_since(self._last_packet_render_step)
       packet.blocks = [
           system_block,        # required
           training_examples,   # required on step 1, delta thereafter
           delta_block,         # compress: replace STATE+ENTITY+MEMORY+PLAN
           current_observation, # required (always full)
           planner_proposals,   # required (always full)
           instruction_block    # required
       ]
       estimated_savings = old_tokens * 0.6  # target 60% reduction
   ```

4. **Telemetry**:
   - Track `compressed_tokens_estimate` vs `full_tokens_estimate` per step
   - Report cumulative token savings

---

## E. Test Patterns

| A# | Test File | Pattern |
|----|-----------|---------|
| 092 | `test_a092_terminal_aligned_meaningful_progress.py` | Flat/regressing/improving terminal distance with object progress; verify classification |
| 093 | `test_a093_fast_prediction_falsification_action_quarantine.py` | 2 high-conf misses → quarantine; TTL expiry; fallback; low-conf non-trigger |
| 094 | `test_a094_multi_action_churn_exhaustion_decision.py` | All actions evidenced + no progress → exhausted; delayed-effect guard defer; early stop |
| 095 | `test_a095_deepseek_prompt_compression.py` | Full prompt step 1; delta step 2+; required blocks retained; 60%+ token drop |

---

## F. Key Code Locations

| Component | File | Method/Line |
|-----------|------|-------|
| Action effect recording | `world_model.py` | `record_effect()` @ 118 |
| Prediction generation | `world_model_planner.py` | `_generate_prediction_for_action()` @ 116 |
| Falsification generation | `world_model_planner.py` | `_generate_falsification_condition_for_action()` @ 180 |
| Candidate ranking | `world_model_planner.py` | `_rank_candidates_by_evidence_backing()` @ 270 |
| Planner selection | `world_model_planner.py` | `select_next_candidate()` @ 326 |
| Reasoning decision | `reasoning_controller.py` | `decide()` @ ~70 |
| Snapshot emission | `runner.py` | `_emit_world_model_decision_snapshot()` @ 3203 |
| Graph queries | `world_model.py` | `get_action_prediction_evidence()` @ 212 |
| Prompt rendering | `orchestrator.py` | `PromptPacket.render()` @ 130 |
| Prompt templates | `prompts.py` | SYSTEM_PROMPT, INSTRUCTION_TEMPLATE, etc. |
| Compiler | `world_model_compiler.py` | `compile()` method (A074 foundational) |

---

## G. Critical Decisions Needed Before Implementation

1. **A092 Decision**: Where should goal model originate?
   - Option A: Pass from orchestrator to compiler (breaks MCP boundary?)
   - Option B: Infer from SolveEngine context (couples world_model to solver)
   - Recommendation: Pass as parameter to `compile()` call

2. **A093 Decision**: High-confidence threshold for quarantine?
   - Option A: 0.7 (matches prior recall confidence default)
   - Option B: 0.8 (stricter, fewer false quarantines)
   - Recommendation: 0.75 + configurable in config.yaml

3. **A094 Decision**: Should exhaustion trigger early stop or reclassification?
   - Option A: Early stop (safe, conservative)
   - Option B: Reclassification (risky, may find hidden patterns)
   - Recommendation: Early stop for now; reclassification as separate card

4. **A095 Decision**: Should compression start at step 1 or step 2?
   - Option A: Step 1 (aggressive, save tokens immediately)
   - Option B: Step 2 (safer, let LLM ground first)
   - Recommendation: Step 2 + full packet on step 1

---

## H. Extension Seams (Where A092-A095 Plug In)

### For A092:
- **Seam 1**: World model compiler must call `grid_analysis.compute_goal_distance()` after each action
- **Seam 2**: Effect nodes must include `alignment_class` property
- **Seam 3**: Planner ranking must prefer terminal_aligned effects

### For A093:
- **Seam 1**: Orchestrator must call `world_model.record_prediction_miss()` after observing mismatch
- **Seam 2**: Planner must query `world_model.is_action_quarantined()` before ranking
- **Seam 3**: Reasoning controller must respect quarantine state

### For A094:
- **Seam 1**: Reasoning controller must call `world_model.check_multi_action_exhaustion()` in decide()
- **Seam 2**: Runner must handle exhaustion decision downstream (early stop)
- **Seam 3**: Telemetry must capture exhaustion decision as JSONL row

### For A095:
- **Seam 1**: Orchestrator must call `world_model.get_delta_since()` on step > 1
- **Seam 2**: Prompt builder must select REQUIRED_BLOCKS + delta block instead of full packet
- **Seam 3**: Telemetry must report compressed token estimate

---

## Summary

**Key insight**: A092-A095 are not isolated features. They form a decision pipeline:
1. **A092** classifies progress as terminal-aligned or local
2. **A093** quarantines actions whose predictions fail
3. **A094** emits exhaustion decision when all paths explored
4. **A095** compresses repeated reasoning about a settled world model

The integration pattern is:
- **Data layer** (world_model.py): Store terminal alignment, prediction misses, quarantine state, delta
- **Decision layer** (reasoning_controller.py): Read data, emit exhaustion decision
- **Presentation layer** (orchestrator.py, prompts.py): Use decision + delta for compression

Start with A092 (cleanest dependencies), then A093 (quarantine), then A094 (decision), then A095 (compression).
