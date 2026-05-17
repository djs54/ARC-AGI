# A092-A095: Quick Start Guide for Implementation

**TL;DR**: Terminal-aligned progress → Fast falsification → Churn decision → Prompt compression

---

## 📋 The Four Cards in 60 Seconds

| Card | Does What | Key Files | Tests |
|------|-----------|-----------|-------|
| **A092** | Classify object progress as terminal-aligned or local-only | grid_analysis.py, world_model_compiler.py | test_a092_terminal_aligned_meaningful_progress.py |
| **A093** | Quarantine actions after 2 failed predictions | world_model.py, world_model_planner.py | test_a093_fast_prediction_falsification_action_quarantine.py |
| **A094** | Emit "exhausted" decision when all actions tried + no progress | reasoning_controller.py, world_model.py | test_a094_multi_action_churn_exhaustion_decision.py |
| **A095** | Compress repeated prompt context using graph deltas | orchestrator.py, prompts.py, world_model.py | test_a095_deepseek_prompt_compression.py |

---

## 🎯 Start Here: A092 (Terminal Alignment)

### What to Build

Three things:
1. **Goal distance computation**: `grid_analysis.compute_goal_distance(obs, goal_model) → float`
2. **Alignment classifier**: `world_model_compiler.classify_effect_alignment(effect, prev, curr) → "terminal_aligned" | "object_local" | "delayed_candidate"`
3. **Effect property**: Add `alignment_class: str` to Effect nodes

### Files to Edit

1. **agents/arc3/grid_analysis.py** (new method ~20 lines)
   ```python
   def compute_goal_distance(observation, goal_model) -> float:
       return goal_model.win_score - observation.terminal_score
   ```

2. **agents/arc3/world_model_compiler.py** (new method ~30 lines + modify compile())
   ```python
   def classify_effect_alignment(effect, prev_obs, curr_obs) -> str:
       goal_dist_improved = (curr_obs.terminal_score - prev_obs.terminal_score) > 0
       if effect.kind == "object_progress" and not goal_dist_improved:
           return "object_local"
       # ... more cases
   ```

3. **agents/arc3/world_model_planner.py** (modify ranking ~5 lines)
   ```python
   # In _rank_candidates_by_evidence_backing():
   if c.predicted_observation.get("alignment_class") == "terminal_aligned":
       evidence_tier = -1  # Highest priority
   ```

### Test First (TDD)

```bash
# Create: tests/test_a092_terminal_aligned_meaningful_progress.py
# Write tests for:
#   - Flat terminal + local object progress → object_local
#   - Improving terminal + object progress → terminal_aligned
#   - Regressing terminal + object progress → object_local
#   - Terminal-aligned candidates rank higher

pytest tests/test_a092_*.py -v
```

### ✅ Done When

- All test cases pass
- `make test-a` still passes
- Telemetry shows `selected_candidate_alignment_tier`

---

## 🎯 Next: A093 (Falsification + Quarantine)

### What to Build

1. **Prediction miss recording**: Graph edge from failed prediction to actual effect
2. **Quarantine state**: Dict tracking which actions are suppressed and for how long
3. **Quarantine checks**: Planner suppresses quarantined actions from exploit tier

### Files to Edit

1. **agents/arc3/world_model.py** (new methods ~60 lines)
   ```python
   def record_prediction_miss(pred_node_id, actual_effect_node_id, confidence):
       # Create edge: Prediction → CONTRADICTED_BY → Effect
       # If 2+ misses: quarantine_action()
   
   def quarantine_action(action_id, ttl=5):
       self._quarantined_actions[action_id] = current_step + ttl
   
   def is_action_quarantined(action_id) -> bool:
       # Check TTL expiry
   ```

2. **agents/arc3/orchestrator.py** (add call ~10 lines)
   ```python
   # After observing action result:
   if prediction_mismatch(selected_candidate, observation):
       world_model.record_prediction_miss(...)
   ```

3. **agents/arc3/world_model_planner.py** (modify ranking ~5 lines)
   ```python
   # In _rank_candidates_by_evidence_backing():
   if c.action_id in quarantined_actions:
       evidence_tier = 3  # Suppress
   ```

### Test First

```bash
# Create: tests/test_a093_fast_prediction_falsification_action_quarantine.py
# Write tests for:
#   - 2 high-conf misses → quarantine
#   - TTL expiry → available again
#   - Low-conf miss → no quarantine
#   - All actions quarantined → fallback to lowest-tier

pytest tests/test_a093_*.py -v
```

### ✅ Done When

- All test cases pass
- `make test-a` still passes
- Telemetry shows `selected_candidate_prediction_missed`, `action_quarantine_count`

---

## 🎯 Then: A094 (Churn Exhaustion Decision)

### What to Build

1. **Exhaustion check**: Method that returns true when all actions evidenced + no terminal progress
2. **Delayed-effect guard**: Don't exhaust if confident prior predicts delayed reward
3. **Decision emission**: Emit `ReasoningMode.MULTI_ACTION_STRATEGY_EXHAUSTED`

### Files to Edit

1. **agents/arc3/world_model.py** (new methods ~30 lines)
   ```python
   def check_multi_action_exhaustion(available_actions, evidence_threshold=2) -> bool:
       # All actions have ≥evidence_threshold effects?
       # No terminal-aligned progress in last N steps?
       # No credible delayed-effect prior?
   
   def has_terminal_aligned_progress(recent_window=5) -> bool:
       # Check effect nodes for alignment_class == "terminal_aligned"
   ```

2. **agents/arc3/reasoning_controller.py** (modify decide() ~30 lines)
   ```python
   # In decide(), after A093 logic:
   if world_model.check_multi_action_exhaustion(available_actions):
       decision = ReasoningDecision(
           mode=ReasoningMode.MULTI_ACTION_STRATEGY_EXHAUSTED,
           world_model_decision="multi_action_churn_exhausted"
       )
   ```

3. **agents/arc3/orchestrator.py** (handle decision ~10 lines)
   ```python
   # After decision returned:
   if decision.world_model_decision == "multi_action_churn_exhausted":
       # Early stop or reclassify
   ```

### Test First

```bash
# Create: tests/test_a094_multi_action_churn_exhaustion_decision.py
# Write tests for:
#   - All actions with evidence + no progress → exhausted
#   - One action missing evidence → not exhausted
#   - Credible delayed prior → defer exhaustion
#   - Decision emitted as JSONL row

pytest tests/test_a094_*.py -v
```

### ✅ Done When

- All test cases pass
- `make test-a` still passes
- Telemetry shows `world_model_decision`, `multi_action_exhaustion_triggered`

---

## 🎯 Finally: A095 (Prompt Compression)

### What to Build

1. **Delta computation**: Return new nodes, edges, hypotheses since last render
2. **Compressed packet**: Render delta instead of full STATE/MEMORY/PLAN on step > 1
3. **Required blocks**: Ensure legal actions, goal, observation, proposals always present

### Files to Edit

1. **agents/arc3/world_model.py** (new method ~40 lines)
   ```python
   def get_delta_since(last_render_step) -> Dict:
       # Return new nodes, edges, hypotheses, contradictions
       # Bounded: limit to most recent
   ```

2. **agents/arc3/prompts.py** (add template + constant ~20 lines)
   ```python
   REQUIRED_BLOCKS = ["SYSTEM", "CURRENT_STATE", "AVAILABLE_ACTIONS", 
                      "PLANNER_PROPOSALS", "INSTRUCTION"]
   
   WORLD_MODEL_DELTA_TEMPLATE = """
   === WORLD MODEL UPDATE ===
   Graph delta: +{nodes} nodes, +{edges} edges
   New contradictions: {contradictions}
   Quarantined: {quarantined}
   Next experiment: {experiment}
   """
   ```

3. **agents/arc3/orchestrator.py** (modify _build_prompt() ~40 lines)
   ```python
   # On step 1: full packet
   # On step > 1: 
   #   delta = world_model.get_delta_since(last_render)
   #   packet = [required_blocks] + [delta_block]
   ```

### Test First

```bash
# Create: tests/test_a095_deepseek_prompt_compression.py
# Write tests for:
#   - Full prompt on step 1, delta on step 2+
#   - Required blocks always present
#   - 60%+ token reduction (estimate)
#   - Legal actions not dropped

pytest tests/test_a095_*.py -v
```

### ✅ Done When

- All test cases pass
- `make test-a` still passes
- Telemetry shows `prompt_compression_enabled`, `prompt_compression_ratio`

---

## 🔧 Code Templates (Copy-Paste Ready)

### A092: Alignment Classification
```python
def classify_effect_alignment(self, effect_node, prev_obs, curr_obs, goal_model):
    """Classify if effect contributes to terminal alignment."""
    effect_kind = effect_node.props.get("kind")
    
    if not goal_model:
        return "unknown"
    
    terminal_improved = curr_obs.terminal_score > prev_obs.terminal_score
    
    if effect_kind == "object_progress":
        if terminal_improved:
            return "terminal_aligned"
        else:
            return "object_local"
    elif effect_kind == "terminal_progress":
        return "terminal_aligned"
    elif effect_kind == "delayed_reward":
        return "delayed_candidate"
    else:
        return "unknown"
```

### A093: Quarantine Methods
```python
def record_prediction_miss(self, prediction_node_id, actual_effect_node_id, confidence):
    self.add_edge(prediction_node_id, "CONTRADICTED_BY", actual_effect_node_id, {
        "miss_confidence": confidence
    })
    self.contradiction_count += 1
    
def quarantine_action(self, action_id: str, ttl: int = 5):
    self._quarantined_actions[action_id] = self._current_step + ttl

def is_action_quarantined(self, action_id: str) -> bool:
    if action_id not in self._quarantined_actions:
        return False
    return self._quarantined_actions[action_id] > self._current_step
```

### A094: Exhaustion Check
```python
def check_multi_action_exhaustion(self, available_actions, evidence_threshold=2):
    for action_id in available_actions:
        effects = self.get_action_effect_table(action_id=action_id)
        if len(effects) < evidence_threshold:
            return False
    
    return not self.has_terminal_aligned_progress(recent_window=5)

def has_terminal_aligned_progress(self, recent_window=5):
    recent_effects = [
        n for n in self.nodes.values()
        if n.label == "Effect" and n.props.get("step", -1) > (self._current_step - recent_window)
    ]
    return any(e.props.get("alignment_class") == "terminal_aligned" for e in recent_effects)
```

### A095: Delta Computation
```python
def get_delta_since(self, last_render_step):
    new_nodes = [
        n for n in self.nodes.values()
        if n.props.get("step", -1) > last_render_step
    ]
    new_edges = [
        e for e in self.edges
        if e.props.get("step", -1) > last_render_step
    ]
    
    return {
        "new_nodes": new_nodes[:5],  # Bounded
        "new_edges": new_edges[:5],
        "new_contradictions": self.contradiction_count,
        "changed_hypotheses": self.get_active_hypotheses(limit=3)
    }
```

---

## 📊 Telemetry Fields to Add

### A092 Fields
- `selected_candidate_alignment_tier: int` (-1=terminal_aligned, 0=predicted, 1=falsifiable, 2=generic, 3=quarantined)
- `terminal_distance_current: float`
- `terminal_distance_previous: float`
- `meaningful_progress_is_terminal_aligned: bool`

### A093 Fields
- `selected_candidate_prediction_missed: bool`
- `selected_candidate_prior_compatibility_score: float`
- `action_quarantine_count: int`

### A094 Fields
- `world_model_decision: str` ("multi_action_churn_exhausted" or null)
- `multi_action_exhaustion_triggered: bool`

### A095 Fields
- `prompt_compression_enabled: bool`
- `prompt_tokens_estimated_full: int`
- `prompt_tokens_estimated_compressed: int`
- `prompt_compression_ratio: float`

---

## ⚠️ Critical Gotchas

1. **Alignment must be computed by compiler, not planner** — Otherwise it's transient and not in graph
2. **Quarantine must check TTL** — `is_action_quarantined()` must return false if current_step >= ttl_step
3. **Exhaustion needs delayed guard** — Don't give up if prior says "delayed_reward" with confidence ≥ 0.7
4. **Compression must preserve required blocks** — Never drop legal actions, current state, or planner proposals
5. **Telemetry fields must match eval schema** — Add to WorldModelStepMetrics before using

---

## 🚀 How to Run Each Phase

```bash
# A092
pytest tests/test_a092_*.py -v && make test-a

# A093  
pytest tests/test_a093_*.py -v && make test-a

# A094
pytest tests/test_a094_*.py -v && make test-a

# A095
pytest tests/test_a095_*.py -v && make test-a

# Full smoke test
make smoke
```

---

## 📞 Quick Reference: Who Calls What

```
orchestrator._execute_action()
  → world_model.record_effect()           [A092: adds alignment_class]
  → orchestrator.check_prediction_match()
    → world_model.record_prediction_miss() [A093]
      → world_model.quarantine_action()    [A093]

reasoning_controller.decide()
  → world_model.check_multi_action_exhaustion() [A094]
    → emit ReasoningMode.MULTI_ACTION_STRATEGY_EXHAUSTED

orchestrator._build_prompt()
  → world_model.get_delta_since()         [A095]
  → PromptPacket with compressed blocks   [A095]
```

---

## ✅ Success Criteria

All of these must be true:

- [ ] `make test-a` passes (baseline green)
- [ ] `pytest tests/test_a092_*.py -v` passes
- [ ] `pytest tests/test_a093_*.py -v` passes
- [ ] `pytest tests/test_a094_*.py -v` passes
- [ ] `pytest tests/test_a095_*.py -v` passes
- [ ] Telemetry fields present in JSONL output
- [ ] Token compression ratio visible in metrics
- [ ] `make smoke` completes without regression

---

## 📚 Full Docs

- Full survey: [A092-A095_CODEBASE_SURVEY.md](A092-A095_CODEBASE_SURVEY.md)
- Method checklist: [A092-A095_IMPLEMENTATION_CHECKLIST.md](A092-A095_IMPLEMENTATION_CHECKLIST.md)
- Code examples: [A092-A095_CODE_PATTERNS.md](A092-A095_CODE_PATTERNS.md)
- Backlog: `backlog/A092.md` through `backlog/A095.md`

---

**Ready? Start with A092 — it's the foundation for everything else!**
