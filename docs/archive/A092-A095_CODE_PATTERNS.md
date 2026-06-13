# A092-A095 Code Patterns & Reference

## 1. How Effects Are Currently Recorded (A092 Extension Pattern)

**Current code in `world_model.py` @ line 118:**

```python
def record_effect(self, action_node_id: str, obs_node_id: str, kind: str, 
                 props: Dict[str, Any]) -> str:
    """Record action → effect → observation causal chain."""
    node_id = f"effect-{self.task_id}-{action_node_id[:8]}-{kind}"
    self.add_node(node_id, "Effect", {
        "kind": kind,
        "magnitude": props.get("magnitude", 0),
        "meaningful": props.get("meaningful", False),
        # ← A092: ADD HERE:
        # "alignment_class": None,  # computed later by compiler
    })
    self.add_edge(action_node_id, "CAUSED", node_id)
    self.add_edge(node_id, "OBSERVED_IN", obs_node_id)
    return node_id
```

**A092 Extension Pattern:**

After compiler runs, alignment should be computed and stored:

```python
# In world_model_compiler.py.compile():
for effect_node in world_model.nodes.values():
    if effect_node.label == "Effect":
        alignment = self.classify_effect_alignment(effect_node, prev_obs, curr_obs)
        effect_node.props["alignment_class"] = alignment
        # This persists to graph for planner ranking
```

---

## 2. How Predictions Are Currently Generated (A093 Extension Pattern)

**Current code in `world_model_planner.py` @ line 116:**

```python
def _generate_prediction_for_action(self, action_id: str, world_model: Any,
                                   prior: Optional[Dict[str, Any]] = None) 
                                   -> Optional[Dict[str, Any]]:
    """Generate predicted observation from prior or graph evidence."""
    
    # Check mechanic prior
    if prior and prior.get("effects"):
        effects = prior.get("effects", [])
        for eff in effects:
            if eff.get("action") == action_id:
                effect_class = self._prior_effect_class(eff)
                if effect_class == "terminal_progress":
                    return {
                        "effect_class": "terminal_progress",
                        "meaningful_progress": True,
                        "confidence": eff.get("confidence", 0.7),
                        "evidence_path": [prior.get("id", "")]
                    }
                # ... more cases
    
    # Fall back to graph evidence (A089)
    pred_evidence = world_model.get_action_prediction_evidence(action_id)
    if pred_evidence and pred_evidence.get("confidence", 0.0) > 0.0:
        return {
            "effect_class": pred_evidence.get("primary_effect_class"),
            "meaningful_progress": True,
            "confidence": pred_evidence.get("confidence"),
            "evidence_path": pred_evidence.get("evidence_path_ids")
        }
    
    return None
```

**A093 Extension Pattern:**

After action result, record miss and trigger quarantine:

```python
# In orchestrator.py, after observing action result:
def check_prediction_match(candidate: PlanCandidate, observation: Observation):
    if not candidate.predicted_observation:
        return  # No prediction, nothing to falsify
    
    pred_class = candidate.predicted_observation.get("effect_class")
    actual_class = observation.effect_class  # from effect classification
    
    if pred_class != actual_class:
        # Record the miss in graph
        world_model.record_prediction_miss(
            prediction_node_id=candidate.predicted_observation.get("evidence_path", [""])[0],
            actual_effect_node_id=observation.node_id,
            confidence=candidate.predicted_observation.get("confidence", 0.0)
        )
        
        # Track for quarantine logic
        if candidate.predicted_observation.get("confidence", 0.0) >= 0.75:
            miss_count = world_model._prediction_miss_counts.get(candidate.action_id, 0)
            if miss_count >= 1:  # Second consecutive miss
                world_model.quarantine_action(candidate.action_id, ttl=5)
            else:
                world_model._prediction_miss_counts[candidate.action_id] = miss_count + 1
```

---

## 3. How Candidates Are Ranked (A090 → A093 Extension Pattern)

**Current ranking code in `world_model_planner.py` @ line 270:**

```python
def _rank_candidates_by_evidence_backing(self, candidates: List[PlanCandidate],
                                        quarantined_actions: Optional[Set[str]] = None
                                        ) -> List[PlanCandidate]:
    """Rank candidates by evidence tier and expected gain."""
    quarantined_actions = set(quarantined_actions or set())
    
    def score_candidate(c: PlanCandidate) -> tuple[int, float, float]:
        evidence_tier = 2
        
        # Check quarantine (A093)
        if c.action_id in quarantined_actions:
            evidence_tier = 3
        # Exploit from productive path
        elif c.mode == PlanMode.EXPLOIT and c.evidence_path.startswith("productive_path:"):
            evidence_tier = -1  # ← A092: terminal_aligned effects should be here
        # Predicted + falsifiable
        elif c.predicted_observation and c.falsification_condition:
            evidence_tier = 0
        # Falsifiable only
        elif c.falsification_condition:
            evidence_tier = 1
        
        # A090: Prior compatibility boost
        prior_boost = c.prior_compatibility_score if c.mechanic_prior_id else 0.0
        
        return (evidence_tier, -prior_boost, -c.expected_gain)
    
    return sorted(candidates, key=score_candidate)
```

**A092 Extension Pattern:**

Add alignment check to tier assignment:

```python
# In score_candidate():
if c.predicted_observation:
    alignment = c.predicted_observation.get("alignment_class")
    if alignment == "terminal_aligned":
        evidence_tier = -1  # Highest priority
    elif alignment == "object_local":
        # Check if terminal distance regressing
        if world_model.terminal_distance_regressing():
            evidence_tier = 3  # Suppress
        else:
            evidence_tier = 1
    elif alignment == "delayed_candidate":
        evidence_tier = 0  # Wait and see
```

---

## 4. How Decisions Are Emitted (A094 Extension Pattern)

**Current decision code in `reasoning_controller.py` @ line ~70:**

```python
def decide(self, world_summary: str, compiled_delta: Optional[Any],
          budget_state: Dict[str, Any], phase: str,
          active_hypotheses: List[Dict[str, Any]],
          available_actions: List[str],
          mechanic_priors: Optional[List[Dict[str, Any]]] = None,
          per_action_evidence: Optional[Dict[str, Any]] = None
          ) -> ReasoningDecision:
    
    # Default to LLM reasoning
    decision = ReasoningDecision(
        mode=ReasoningMode.LLM_REASON, 
        trigger="default_escalation",
        stall_threshold=self._stall_threshold
    )
    
    failure_signal = getattr(compiled_delta, "failure_signal", None)
    
    # ... many checks for different modes
    
    return decision
```

**A094 Extension Pattern:**

Add exhaustion check after A093 logic:

```python
# In decide(), after other checks, near end:

# A094: Multi-action churn exhaustion
if per_action_evidence and len(available_actions) > 1:
    all_actions_have_evidence = all(
        per_action_evidence.get(a, {}).get("tested_count", 0) >= 2
        for a in available_actions
    )
    
    # Check for terminal-aligned progress
    has_terminal_progress = any(
        effect.get("alignment_class") == "terminal_aligned"
        for action_id in available_actions
        for effect in world_model.get_action_effects(action_id)
    )
    
    if all_actions_have_evidence and not has_terminal_progress:
        # Check delayed-effect guard
        has_credible_delayed = any(
            p.get("confidence", 0.0) >= 0.7 and p.get("predicts_delayed_reward")
            for p in (mechanic_priors or [])
        )
        
        if not has_credible_delayed:
            decision = ReasoningDecision(
                mode=ReasoningMode.MULTI_ACTION_STRATEGY_EXHAUSTED,
                trigger="all_actions_exhausted",
                world_model_decision="multi_action_churn_exhausted",
                multi_action_churn_detected=True
            )
            return decision

return decision
```

**How runner captures the decision:**

In `runner.py` @ line 3203:

```python
def _emit_world_model_decision_snapshot(self, decision: ReasoningDecision, **kwargs):
    """Emit world model decision to telemetry."""
    snapshot = {
        "step": self._current_step,
        "reasoning_mode": str(decision.mode),
        "trigger": decision.trigger,
        "world_model_decision": decision.world_model_decision,  # ← captures "multi_action_churn_exhausted"
        "multi_action_churn_detected": decision.multi_action_churn_detected,
        # ... other fields
    }
    self._emit_trace_event("world_model_decision", "decision_snapshot", snapshot)
```

---

## 5. How Prompt Packets Are Built (A095 Extension Pattern)

**Current packet rendering in `orchestrator.py` @ line 130:**

```python
class PromptPacket:
    """A typed collection of content blocks."""
    blocks: List[ContentBlock] = field(default_factory=list)
    
    def render(self) -> str:
        """Render the packet into a final prompt string."""
        ordered_keys = [
            "SYSTEM", "TRAINING_EXAMPLES", "SOLVED_LEVELS", "PRIOR_INSIGHTS",
            "GRID_ANALYSIS", "REPL_RESULTS",
            "STATE", "ENTITY_CONTEXT", "MEMORY", "SOLVE_CONTEXT", "NAVIGATION", "PLAN",
            "ACTION_FACTS", "EXPLORATION_SUMMARY", "PATH_HYPOTHESES", "HYPOTHESIS",
            "PATTERN_HYPOTHESIS", "REASONING_WORKSPACE", "GRID", "TEST_INPUT",
            "OBSERVED_EFFECTS", "REFLEX", "HISTORY", "OBSERVATION",
            "INSTRUCTION", "ACTION_INVOCATION"
        ]
        
        block_map = {b.type: b for b in self.blocks}
        final_parts = []
        
        for key in ordered_keys:
            if key in block_map:
                block = block_map[key]
                if not block.content.strip():
                    continue
                
                # Render with or without header
                if key in {"SYSTEM", "STATE", "INSTRUCTION", "ACTION_INVOCATION"}:
                    final_parts.append(f"{key}: {block.content}")
                else:
                    header = block.header or headers.get(key)
                    if header:
                        final_parts.append(f"=== {header} ===\n{block.content}")
        
        return "\n\n".join(final_parts)
```

**A095 Extension Pattern:**

Introduce compressed rendering:

```python
# In prompts.py, add new template:
WORLD_MODEL_DELTA_TEMPLATE = """
=== WORLD MODEL UPDATE (compressed) ===
Graph delta (step {last_step}→{current_step}):
  New nodes: +{new_node_count} ({new_node_types})
  New edges: +{new_edge_count} ({new_edge_types})
  
Active contradictions: {contradiction_summary}
Quarantined actions: {quarantine_list}

Next planner experiment: {experiment_description}
"""

REQUIRED_BLOCKS = ["SYSTEM", "CURRENT_STATE", "AVAILABLE_ACTIONS", 
                   "PLANNER_PROPOSALS", "INSTRUCTION"]

# In orchestrator.py, modify _build_prompt():
def _build_prompt_packet(self) -> PromptPacket:
    packet = PromptPacket()
    
    # On step 1: full packet
    if self._current_step == 1:
        packet.blocks = [
            ContentBlock("SYSTEM", self.SYSTEM_PROMPT),
            ContentBlock("TRAINING_EXAMPLES", ...),
            ContentBlock("STATE", ...),
            ContentBlock("ENTITY_CONTEXT", ...),
            ContentBlock("MEMORY", ...),
            # ... full blocks
        ]
        self._last_packet_render_step = 1
    
    # On step > 1: compressed packet
    else:
        delta = self.world_model.get_delta_since(self._last_packet_render_step)
        
        delta_content = WORLD_MODEL_DELTA_TEMPLATE.format(
            last_step=self._last_packet_render_step,
            current_step=self._current_step,
            new_node_count=len(delta.get("new_nodes", [])),
            new_node_types=...,
            new_edge_count=len(delta.get("new_edges", [])),
            new_edge_types=...,
            contradiction_summary=...,
            quarantine_list=...,
            experiment_description=self._last_planner_selection.rationale
        )
        
        packet.blocks = [
            ContentBlock("SYSTEM", self.SYSTEM_PROMPT),
            ContentBlock("TRAINING_EXAMPLES", ...),  # or reference to step 1
            ContentBlock("WORLD_MODEL_DELTA", delta_content),
            ContentBlock("CURRENT_STATE", ...),  # ← always full
            ContentBlock("PLANNER_PROPOSALS", ...),  # ← always full
            ContentBlock("INSTRUCTION", ...),  # ← always full
        ]
        self._last_packet_render_step = self._current_step
    
    return packet
```

---

## 6. How Graph Queries Work (for A089 basis)

**Example: `get_action_prediction_evidence()` in `world_model.py` @ line 212:**

```python
def get_action_prediction_evidence(self, action_id: str, limit: int = 5) -> Dict[str, Any]:
    """Get bounded prediction evidence for an action from graph history."""
    result = {
        "action_id": action_id,
        "effect_histogram": {},
        "meaningful_progress_rate": 0.0,
        "contradiction_count": 0,
        "evidence_path_ids": [],
        "confidence": 0.0
    }
    
    # Find recent action nodes
    action_nodes = [
        n for n in self.nodes.values() 
        if n.label == "Action" and n.props.get("action_id") == action_id
    ]
    
    if not action_nodes:
        return result
    
    # Sort by step, get most recent
    action_nodes.sort(key=lambda n: n.props.get("step", -1), reverse=True)
    action_nodes = action_nodes[:limit]
    
    total_effects = 0
    meaningful_effects = 0
    evidence_ids = set()
    
    for act in action_nodes:
        evidence_ids.add(act.id)
        # Find CAUSED effects
        effect_indices = self._out_edges.get(act.id, [])
        for idx in effect_indices:
            edge = self.edges[idx]
            if edge.rel == "CAUSED":
                eff = self.nodes.get(edge.dst)
                if eff:
                    effect_kind = eff.props.get("kind", "unknown")
                    result["effect_histogram"][effect_kind] = \
                        result["effect_histogram"].get(effect_kind, 0) + 1
                    
                    if eff.props.get("meaningful"):
                        meaningful_effects += 1
                    total_effects += 1
    
    if total_effects > 0:
        result["meaningful_progress_rate"] = meaningful_effects / total_effects
        result["confidence"] = min(1.0, result["meaningful_progress_rate"] * 0.9)
    
    result["evidence_path_ids"] = list(evidence_ids)[:5]  # Bounded
    
    return result
```

---

## 7. Telemetry Integration (for eval.py)

**How metrics are captured in `benchmarks/arc3/world_model_eval.py`:**

```python
class WorldModelStepMetrics:
    # A090 fields already exist:
    selected_candidate_has_prediction: bool = False
    selected_candidate_has_falsification: bool = False
    planner_selected_prior_id: Optional[str] = None
    
    # A092-A095 additions:
    selected_candidate_alignment_tier: int = 2
    terminal_distance_current: float = 0.0
    terminal_distance_previous: float = 0.0
    meaningful_progress_is_terminal_aligned: bool = False
    
    # A093 additions:
    selected_candidate_prediction_missed: bool = False
    selected_candidate_prior_compatibility_score: float = 0.0
    action_quarantine_count: int = 0
    
    # A094 additions:
    world_model_decision: Optional[str] = None
    multi_action_exhaustion_triggered: bool = False
    
    # A095 additions:
    prompt_compression_enabled: bool = False
    prompt_tokens_estimated_full: int = 0
    prompt_tokens_estimated_compressed: int = 0
    prompt_compression_ratio: float = 0.0
```

---

## 8. Config Schema (typical additions)

```yaml
# In config.yaml under reasoning_gate section:

reasoning_gate:
  # A093: Falsification + Quarantine
  falsification_confidence_threshold: 0.75  # Min confidence to record miss
  falsification_miss_count_for_quarantine: 2  # Misses before quarantine
  falsification_quarantine_ttl_steps: 5  # How long to suppress
  
  # A094: Churn Exhaustion
  multi_action_exhaustion_evidence_threshold: 2  # Effects per action
  multi_action_exhaustion_include_delayed_check: true
  multi_action_exhaustion_early_stop: true  # Stop vs reclassify
  
  # A095: Prompt Compression
  prompt_compression_enabled: true
  prompt_compression_start_step: 2  # Full packet on step 1
  prompt_compression_target_ratio: 0.4  # Goal: 40% of full tokens
```

---

## 9. Testing Utilities

**Common test setup pattern:**

```python
from agents.arc3.world_model import WorldModelGraph
from agents.arc3.world_model_compiler import WorldModelCompiler
from agents.arc3.world_model_planner import WorldModelPlanner

def setup_test_world():
    world = WorldModelGraph(task_id="test", session_id="sess123")
    compiler = WorldModelCompiler()
    planner = WorldModelPlanner()
    return world, compiler, planner

def record_action_and_effect(world, step, action_id, effect_class, meaningful=True):
    state_id = world.record_state(step, f"hash{step}")
    action_id_node = world.record_action(step, action_id, {}, state_id)
    obs_id = world.add_node(f"obs-{step}", "Observation", {"step": step})
    effect_id = world.record_effect(action_id_node, obs_id, effect_class, {
        "magnitude": 5 if meaningful else 1,
        "meaningful": meaningful
    })
    return state_id, action_id_node, effect_id
```

---

## 10. Common Mistakes to Avoid

1. **Don't forget to mark nodes as `"label": "EffectMiss"` separately** — record misses as edges, not separate nodes (unless modeling explicit predictions as nodes too)

2. **Quarantine state must check expiry** — include step expiry check in `is_action_quarantined()`

3. **Delayed-effect guard must read priors** — check `mechanic_priors[]` for confidence threshold before declaring exhaustion

4. **Compression must preserve required blocks** — never drop SYSTEM, CURRENT_STATE, AVAILABLE_ACTIONS, PLANNER_PROPOSALS, INSTRUCTION

5. **Telemetry field names must match eval schema** — use exact names in ReasoningDecision and snapshot

6. **Terminal distance needs baseline** — first step has no "previous" distance; compute delta starting step 2

7. **Alignment class should be transient** — computed by compiler, not stored on Action node (store on Effect)

8. **Quarantine check must happen in planner ranking, not just orchestrator** — planner needs visibility to suppress during `select_next_candidate()`
