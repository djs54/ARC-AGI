# ARC Agent v2 — Refactor Plan

> Implementation plan for the graph-driven workflow architecture.
> See `refactorLearning.md` for the analysis and rationale behind each decision.

---

## Architecture Overview

### Design Principles

1. **The graph decides, the LLM advises.** Durable belief state belongs to the
   world model graph, not to LLM context. The LLM generates hypotheses and
   proposes experiments, but the graph holds what is believed, what is
   falsified, and what experiment is worth paying for next.

2. **MCP seam only.** All communication between the ARC agent and Campy goes
   through the MCP stdio adapter. No direct imports. No exceptions.

3. **Separate agents for separate concerns.** Planning, vetting, execution,
   and evaluation are independent modules with clear interfaces. Each reads
   from and writes to the graph. No module grades its own work.

4. **Brain-region alignment.** Agent modules map to brain regions in Campy's
   architecture: Sensory Cortex (perceive), Hippocampus (memory), Temporal
   Lobe (goal/concept), Prefrontal Cortex (plan/LLM), Basal Ganglia
   (action gate), Thalamus (orchestrate), Brainstem (governance).

5. **Deterministic where possible, LLM where needed.** Graph queries and
   heuristics handle the common path. LLM reasoning is reserved for
   hypothesis generation, plan generation, and ambiguity resolution.

6. **Parallel prototype.** The new agent plugs into the existing
   `run_single_puzzle.py` entry point alongside the current orchestrator.
   The current agent remains as a fallback until the prototype proves itself.

---

## Layer Architecture

```
+---------------------------------------------------------------+
| Layer 1: GAME HARNESS (existing, unchanged)                   |
|   run_single_puzzle.py, runner.py, ARC API adapter             |
+---------------------------------------------------------------+
| Layer 2: WORKFLOW ORCHESTRATOR (new: ~500 lines)              |
|   Thalamus role: routes, gates, dispatches. Does NOT reason.  |
|   State machine: PERCEIVE -> RESOLVE -> PLAN -> VET ->        |
|                  EXECUTE -> EVALUATE -> [loop]                 |
+---------------------------------------------------------------+
| Layer 3: AGENT MODULES (new: brain-region aligned)            |
|   Sensory Cortex:    PerceiveAgent                            |
|   Temporal Lobe:     GoalResolver                             |
|   Prefrontal Cortex: PlanGenerator (LLM lives here)           |
|   Basal Ganglia:     PlanVetter (Go/No-Go gate)               |
|   Motor Cortex:      Executor                                 |
|   Temporal Lobe:     Evaluator                                |
+---------------------------------------------------------------+
| Layer 4: DETERMINISTIC TOOLS (existing, refactored)           |
|   SolveEngine, GoalHypothesisDetector, HybridPatternMatcher,  |
|   GridAnalysis — pure functions, no graph, no LLM             |
+---------------------------------------------------------------+
| Layer 5: MEMORY (existing: HippoCampy via MCP)                |
|   World model graph + new ARC-specific MCP query tools        |
|   Hippocampus: episodic memory, entity tracking               |
|   Temporal Lobe: concept/archetype classification              |
|   Basal Ganglia: action selection, reward prediction           |
+---------------------------------------------------------------+
| Cross-cutting: GOVERNANCE (Brainstem)                         |
|   Budget guard, exploration gate, stall detector,              |
|   falsification tracker, telemetry emission                    |
+---------------------------------------------------------------+
```

---

## Core Loop

```
+-------------------------------------------------------------+
|                    GAME LOOP (per puzzle)                     |
|                                                              |
|  +----------+    +--------------+    +----------------+     |
|  | PERCEIVE |-->| RESOLVE GOAL |-->| GENERATE PLAN  |     |
|  +----------+    +--------------+    +-------+--------+     |
|       ^                                      |              |
|       |                             +--------v--------+     |
|       |                             |    VET PLAN     |     |
|       |                             | (basal ganglia) |     |
|       |                             +---+--------+----+     |
|       |                         approved|        |blocked   |
|       |                                 v        v          |
|       |                             +------+  +------+     |
|       |                             |EXECUTE|  |REPLAN|-+   |
|       |                             +--+---+  +------+ |   |
|       |                                |               |   |
|       |                             +--v--------+      |   |
|       |                             | EVALUATE   |      |   |
|       |                             | (temporal) |      |   |
|       |                             +--+----+----+      |   |
|       |                       continue |    | pivot     |   |
|       +--------------------------------+    +-----------|   |
|                                                              |
|  ============================================================|
|  |              WORLD MODEL GRAPH (durable state)           ||
|  |  Goals - Hypotheses - Actions - Effects - Mechanics      ||
|  ============================================================|
+-------------------------------------------------------------+
```

### Orchestrator State Machine

```python
# Pseudocode for agents/arc4/workflow.py

class GameWorkflow:
    state: { step, phase, budget, game_id, goal }

    async def run(self, game, max_steps):
        for step in range(max_steps):
            # 1. PERCEIVE (Sensory Cortex)
            perception = await self.perceive(game.observation)

            # 2. RESOLVE GOAL (Temporal Lobe)
            goal = await self.resolve_goal()

            # 3. GENERATE PLAN (Prefrontal Cortex / LLM)
            plan = await self.generate_plan(goal)

            # 4. VET PLAN (Basal Ganglia — Go/No-Go)
            vet_result = await self.vet_plan(plan)
            if vet_result.blocked:
                plan = await self.replan(vet_result.reason)
                vet_result = await self.vet_plan(plan)  # one retry
                if vet_result.blocked:
                    continue  # skip to next step

            # 5. EXECUTE (Motor Cortex)
            result = await self.execute(plan)

            # 6. EVALUATE (Temporal Lobe — adversarial)
            evaluation = await self.evaluate(result, goal)

            if evaluation.decision == "terminate":
                break
            elif evaluation.decision == "pivot":
                continue  # re-resolve goal next step
```

### Gates (Brainstem / Governance)

| Gate | Trigger | Action |
|---|---|---|
| Budget guard | `step >= max_steps` | Terminate |
| Exploration gate | Untested actions exist AND tested actions falsified 2+ times | Block tested action, force untested |
| Stall detector | 3+ consecutive steps with no meaningful progress | Escalate to LLM reasoning or terminate |
| Crash guard | Unhandled exception in any phase | Capture full traceback, set `failure_class=crash` |

---

## Agent Module Specifications

### PerceiveAgent (Sensory Cortex)

**Input:** Game observation (grid state, reward, terminal status, step number)

**Process:**
1. Extract grid entities (color regions, positions, roles)
2. Compute grid hash for loop detection
3. Compare to previous GridSnapshot for delta
4. Identify entity movements, appearances, disappearances

**Output:** Graph mutations via MCP:
- `arc_perceive_state` → creates GridSnapshot, updates GridEntity nodes,
  creates ActionEffect for the previous action

**LLM escalation:** None. Purely deterministic.

### GoalResolver (Temporal Lobe — tiered)

**Input:** Current graph state (via MCP queries)

**Process — three tiers:**

**Tier 1 — Heuristic (every step, cheap):**
- Check structural patterns: color groups, spatial layout, symmetry
- Use existing `GoalHypothesisDetector` from Layer 4
- Creates/updates VictoryCondition nodes with initial confidence

**Tier 2 — Graph query (every step):**
- `arc_get_goal_evidence` → VictoryCondition + INFERRED_FROM edges
- `arc_classify_game_archetype` → match to known Concept archetypes
- Score goals by: `confidence = (supports - contradicts) / total_evidence`
- Grounding gate: confidence increases require meaningful progress
- Promote leading hypothesis, demote contradicted ones

**Tier 3 — LLM escalation (when needed):**
- Trigger: top-2 goal confidences within 0.1, all goals below 0.3,
  or 3+ steps with no goal resolution
- LLM analyzes game state and proposes/refines goal hypothesis
- Result written back to graph via `arc_update_goal_confidence`

**Output:** `{ goal: VictoryCondition, confidence: float, tier_used: int }`

### PlanGenerator (Prefrontal Cortex)

**Input:** Resolved goal + graph evidence

**Process:**
1. `arc_get_untested_actions` → exploration candidates
2. `arc_get_action_evidence` → tested action track records
3. `arc_get_causal_path` → does action lead toward goal?
4. `arc_get_mechanic_priors` → cross-game mechanic transfer
5. Rank candidates:
   - Priority 1: Untested actions (exploration)
   - Priority 2: Actions with causal path to goal
   - Priority 3: Actions with mechanic prior support
   - Priority 4: Fallback probes
6. Generate prediction and falsification condition for selected action

**LLM escalation:** When no graph evidence exists and action space is large,
invoke LLM to propose an experiment.

**Output:** `PlanCandidate { action_id, args, predicted_effect, falsification_condition, evidence_path }`

### PlanVetter (Basal Ganglia — Go/No-Go Gate)

**Input:** Proposed plan + graph evidence

**Process — five graph queries:**

1. **Falsification check:** `arc_check_action_gate(action_id)`
   → Has this action been falsified 3+ times? If yes AND alternatives exist → BLOCK

2. **Exploration check:** `arc_get_untested_actions()`
   → Are untested alternatives available? If yes AND this action tried 2+ times → BLOCK

3. **Goal alignment check:** `arc_get_causal_path(action_id, goal_id)`
   → Does this action serve the current goal? If no path → FLAG (warn, don't block)

4. **Strategy repeat check:** `arc_get_action_evidence(action_id)`
   → Is this a repeat of a failed strategy (same family, same failure pattern)?

5. **Reward prediction:** `arc_check_action_gate(action_id)`
   → Predicted reward vs historical actual reward. Large negative delta → BLOCK

**LLM escalation:** None. Purely deterministic graph queries. If the graph
can't answer, the answer is "insufficient evidence, proceed with caution."

**Output:** `{ approved: bool, reason: str, alternative_suggestion: str | null }`

### Executor (Motor Cortex)

**Input:** Approved PlanCandidate

**Process:** Call game API with action_id and args

**Output:** Game observation (grid, reward, terminal, step count)

**No LLM. No graph access.** Pure side-effect execution.

### Evaluator (Temporal Lobe — Adversarial Review)

**Input:** Execution result + goal + plan (with prediction)

**Process:**
1. Compare predicted effect against actual effect
2. Compute terminal grounded score (SolveEngine from Layer 4)
3. `arc_get_entity_movement` → did entities move toward goal?
4. If prediction was wrong → `arc_contradict_hypothesis` + `arc_record_reward_prediction_error`
5. If prediction was right → `arc_confirm_hypothesis`
6. Update ActionFact via `arc_record_action_effect`
7. Update goal confidence via `arc_update_goal_confidence`
8. Track class-level falsification counts

**LLM escalation:** When effect is ambiguous (neither helpful nor harmful),
invoke LLM to interpret and propose hypothesis update.

**Output:** `{ decision: continue | pivot | terminate, reason: str, graph_mutations: [...] }`

---

## MCP Query Tools (Campy-side)

### Brain-Region-Aligned Tool Surface

All tools are added to `campy/brain/thalamus/tools/arc_queries.py` and
registered in `TOOL_HANDLERS`. Each is a bounded Cypher traversal over the
existing ARC graph schema — no new node types needed.

#### Sensory Cortex Tools

| Tool | Signature | Returns |
|---|---|---|
| `arc_perceive_state` | `{task_id, step, grid_hash, entities[], action_taken, effect}` | `{snapshot_id, entity_count, delta_from_previous}` |

#### Hippocampus Tools (Episodic Memory)

| Tool | Signature | Returns |
|---|---|---|
| `arc_get_game_context` | `{task_id}` | `{goal, action_summary, hypothesis_count, step, progress_trend}` |
| `arc_get_action_evidence` | `{task_id, action_id}` | `{tested, falsified_count, causal_power, value_status, steps_used}` |
| `arc_get_untested_actions` | `{task_id, available_actions[]}` | `{untested: [], tested: []}` |
| `arc_get_causal_path` | `{task_id, action_id, goal_id}` | `{path_exists, path_length, path_confidence}` |
| `arc_record_action_effect` | `{task_id, action_id, step, effect, entities_affected[]}` | `{status, fact_id, effect_id}` |
| `arc_get_entity_movement` | `{task_id, step}` | `{entities: [{id, moved_toward_goal, distance_delta}]}` |

#### Temporal Lobe Tools (Semantic Memory / Concepts)

| Tool | Signature | Returns |
|---|---|---|
| `arc_get_goal_evidence` | `{task_id}` | `{goals: [{type, confidence, supports, contradicts}]}` |
| `arc_classify_game_archetype` | `{task_id, grid_features}` | `{archetype, confidence, matching_concepts[]}` |
| `arc_confirm_hypothesis` | `{task_id, hypothesis_id, evidence}` | `{status, new_confidence}` |
| `arc_contradict_hypothesis` | `{task_id, hypothesis_id, evidence}` | `{status, new_confidence, falsified}` |
| `arc_update_goal_confidence` | `{task_id, goal_id, new_confidence, has_meaningful_progress}` | `{status, gated_confidence}` |
| `arc_get_mechanic_priors` | `{task_id, action_patterns[], game_features}` | `{mechanics: [{name, confidence, patterns, effects}]}` |

#### Basal Ganglia Tools (Action Selection / Reward)

| Tool | Signature | Returns |
|---|---|---|
| `arc_check_action_gate` | `{task_id, action_id, available_actions[]}` | `{go: bool, reason, falsification_count, reward_prediction_error, untested_available}` |
| `arc_record_reward_prediction_error` | `{task_id, action_id, step, predicted_reward, actual_reward}` | `{status, cumulative_error}` |

---

## File Structure

### ARC_AGI repo (new files)

```
agents/arc4/                        # v2 agent package
agents/arc4/__init__.py
agents/arc4/workflow.py             # Layer 2: orchestrator state machine (~500 lines)
agents/arc4/perceive.py             # Sensory Cortex: observation -> graph
agents/arc4/goal_resolver.py        # Temporal Lobe: tiered goal system
agents/arc4/plan_generator.py       # Prefrontal Cortex: action planning
agents/arc4/plan_vetter.py          # Basal Ganglia: Go/No-Go gate
agents/arc4/executor.py             # Motor Cortex: action execution
agents/arc4/evaluator.py            # Temporal Lobe: adversarial review
agents/arc4/graph_queries.py        # MCP query wrapper (thin MCPBrainClient calls)
agents/arc4/telemetry.py            # Telemetry emission (live JSONL, world model JSONL)

tests/test_arc4_workflow.py         # Orchestrator state machine tests
tests/test_arc4_plan_vetter.py      # PlanVetter Go/No-Go gate tests
tests/test_arc4_goal_resolver.py    # GoalResolver tiered system tests
tests/test_arc4_evaluator.py        # Evaluator adversarial review tests
```

### HippoCampy repo

**B277 (in progress)** creates the basal ganglia brain region foundation:

```
campy/brain/basal_ganglia/__init__.py           # 6th brain region (B277)
campy/brain/basal_ganglia/frustration_clusters.py # Extracted from sweep.py (B277)
campy/brain/basal_ganglia/procedure_synthesis.py  # Extracted from sweep.py (B277)
campy/brain/basal_ganglia/procedure_maturity.py   # Lifecycle management (B277)
campy/brain/basal_ganglia/action_selector.py      # General Go/No-Go logic (B277)
campy/brain/basal_ganglia/reward_predictor.py     # General RPE tracking (B277)
campy/brain/basal_ganglia/exploration_policy.py   # Explore vs exploit (B277)
```

**Follow-up card (post-B277)** adds ARC-specific MCP query tools:

```
campy/brain/thalamus/tools/arc_queries.py   # All 14 ARC MCP query tools
  - arc_check_action_gate          → calls basal_ganglia/action_selector + ARC ActionFact queries
  - arc_record_reward_prediction_error → calls basal_ganglia/reward_predictor + ARC graph targets
  - arc_perceive_state             → Sensory Cortex: grid observation ingestion
  - arc_get_game_context           → Hippocampus: episodic summary
  - arc_get_action_evidence        → Hippocampus: action track record
  - arc_get_untested_actions       → Hippocampus: untested action list
  - arc_get_causal_path            → Hippocampus: bounded action-to-goal traversal
  - arc_record_action_effect       → Hippocampus: write action effect
  - arc_get_entity_movement        → Hippocampus: entity position tracking
  - arc_get_goal_evidence          → Temporal Lobe: goal evidence query
  - arc_classify_game_archetype    → Temporal Lobe: game type classification
  - arc_confirm_hypothesis         → Temporal Lobe: write evidence support
  - arc_contradict_hypothesis      → Temporal Lobe: write evidence contradiction
  - arc_update_goal_confidence     → Temporal Lobe: gated confidence update
  - arc_get_mechanic_priors        → Temporal Lobe: cross-game transfer

tests/test_arc_queries.py                   # ARC MCP tool tests
```

**Dependency chain:**

```
B277 (basal ganglia foundation — in progress)
  |
  v
ARC MCP Query Tools card (new — registers 14 tools in TOOL_HANDLERS)
  |                        calls into basal_ganglia/ + hippocampus/ + thalamus/
  |
  v (MCP seam)
ARC_AGI agents/arc4/ (Phase 1 prototype — calls tools via MCPBrainClient)
```

---

## Migration Phases

### Phase 1: Parallel Prototype (2-3 weeks)

**Goal:** Prove the architecture on the SU15 puzzle.

1. Create `agents/arc4/` package
2. Implement `workflow.py` — the orchestrator state machine
3. Implement `perceive.py` — wraps existing GridAnalysis + MCP writes
4. Implement `goal_resolver.py` — Tier 1 (heuristics from existing GoalHypothesisDetector), Tier 2 (stubbed graph queries), Tier 3 (LLM escalation)
5. Implement `plan_generator.py` — uses existing WorldModelPlanner ranking logic + MCP queries
6. Implement `plan_vetter.py` — 5 graph queries, deterministic Go/No-Go
7. Implement `executor.py` — thin wrapper around game API
8. Implement `evaluator.py` — wraps existing SolveEngine + MCP writes
9. Wire into `run_single_puzzle.py` with `--agent-version=v2` flag
10. Run on SU15 puzzle, compare to v1

**Success criteria:** v2 tries ACTION7 (v1 never did). v2 has a goal
hypothesis by step 2. v2 does not crash with UnknownCrash.

### Phase 2: MCP Query Tools (1-2 weeks, parallel with Phase 1)

**Goal:** Build the brain-region-aligned MCP tool surface in Campy.

**Prerequisite:** B277 (basal ganglia extraction) must be complete. B277 is
in progress — a subagent is working on it now. It creates
`campy/brain/basal_ganglia/` with `action_selector.py`, `reward_predictor.py`,
and `exploration_policy.py` as the foundation modules.

**What B277 provides (foundation — no action needed here):**
- `check_action_gate()` — general Go/No-Go logic over Procedure nodes
- `record_reward_prediction_error()` — general RPE on Plan nodes
- `should_explore()` — general exploration/exploitation via vector search
- Frustration cluster detection and procedure synthesis (extracted from sweep.py)
- Procedure maturity lifecycle

**What this phase adds (ARC-specific MCP tools on top of B277):**

1. Create `campy/brain/thalamus/tools/arc_queries.py`
2. Implement Hippocampus tools (6 tools):
   - `arc_get_game_context`, `arc_get_action_evidence`,
     `arc_get_untested_actions`, `arc_get_causal_path`,
     `arc_record_action_effect`, `arc_get_entity_movement`
   - These query `ActionFact`, `ActionEffect`, `GridEntity`, `GridSnapshot`
     nodes scoped by `task_id`
3. Implement Temporal Lobe tools (6 tools):
   - `arc_get_goal_evidence`, `arc_classify_game_archetype`,
     `arc_confirm_hypothesis`, `arc_contradict_hypothesis`,
     `arc_update_goal_confidence`, `arc_get_mechanic_priors`
   - These query `VictoryCondition`, `Hypothesis`, `Concept`, `ArcMechanic`
     nodes and their evidence edges
4. Implement Basal Ganglia tools (2 tools):
   - `arc_check_action_gate` — wraps B277's `check_action_gate()` but adds
     ARC-specific queries: `ActionFact` contradiction counts by `task_id`,
     untested action availability, step-scoped falsification history
   - `arc_record_reward_prediction_error` — wraps B277's
     `record_reward_prediction_error()` but writes to ARC-specific graph
     targets (`ActionFact`, `ArcWorldModelStep`) instead of Plan nodes
5. Implement Sensory Cortex tool (1 tool):
   - `arc_perceive_state` — creates `GridSnapshot`, updates `GridEntity` nodes
6. Register all 14 tools in `TOOL_HANDLERS` (alongside B277's general tools)
7. Test through MCP stdio adapter end-to-end
8. Add `arc_*` methods to `MCPBrainClient` in ARC_AGI

**Success criteria:** All 14 tools callable through MCP. Each returns
structured JSON, not prose. Bounded Cypher traversals (no unbounded paths).
B277's general basal ganglia modules remain independent — ARC tools call into
them but don't modify them.

### Phase 3: Validation and Comparison (1 week)

**Goal:** Prove v2 matches or exceeds v1.

1. Run v1 and v2 on the same set of 5 smoke puzzles
2. Compare metrics:
   - Steps to solve (or steps before crash/stall)
   - Number of distinct actions tried
   - Goal accuracy (did the agent identify the correct goal?)
   - Crash rate
   - Memory utilization (was `memory_transfer_state` ever non-zero?)
3. If v2 matches or exceeds v1 on 3+ metrics → proceed to Phase 4
4. If not → identify specific gap, iterate on the weakest agent module

### Phase 4: Migration (2+ weeks)

**Goal:** Make v2 the default and archive v1.

1. Port remaining v1 features:
   - Telemetry emission (live JSONL, world model JSONL)
   - Cost tracking
   - Failure taxonomy and classification
   - Traceback capture (A112)
2. Update `run_single_puzzle.py` to default to `--agent-version=v2`
3. Archive `agents/arc3/orchestrator.py` (keep for reference, remove from hot path)
4. Update backlog: re-map relevant A-series cards, archive obsoleted cards
5. Update ARCHITECTURE.md with v2 layer model

---

## Non-Goals

- Do not tune LLM prompts in this plan. Focus on architecture.
- Do not change the game API or harness.
- Do not import Campy internals. MCP seam only.
- Do not remove the v1 agent until v2 is proven on multiple puzzles.
- Do not add MCP calls to the execute hot path (Executor has no graph access).
- Do not store more raw text when a graph update is enough.
- Do not create new graph node types — the existing schema is sufficient.
- Do not build the full 14-tool MCP surface before the prototype works with stubs.

---

## Dependencies

| Dependency | Status | Notes |
|---|---|---|
| Campy MCP server running | Required | Fixed `_with_phase` bug (2026-05-29). Daemon must be restarted. |
| **B277: Basal ganglia extraction** | **Complete** | 6 brain regions live. 857 lines, 26 tests. `action_selector.py`, `reward_predictor.py`, `exploration_policy.py` ready for ARC tools to build on. |
| Existing ARC graph schema in Campy | Ready | 49 node types, ~45 relationships, ARC-specific nodes already exist |
| `MCPBrainClient` in ARC_AGI | Ready | 29 methods, tested and working |
| Existing deterministic tools (SolveEngine, etc.) | Ready | Pure functions, no changes needed for Phase 1 |
| `run_single_puzzle.py` entry point | Ready | Needs `--agent-version` flag added |

### Dependency Flow

```
B277 (Campy: basal ganglia foundation)     ← COMPLETE
  |
  +-- B278 / Phase 2: ARC MCP Query Tools  ← COMPLETE (15/15 tools live)
  |
  +-- A118-A123 / Phase 1: ARC v2 Prototype ← COMPLETE (2786 lines, 32 tests, smoke ran)
        |
        +-- A124: Fix LLM prompt + stall   ← COMPLETE (LLM adapter hardened, stall guard tuned)
        |
        +-- A125 / Phase 3: Validation     ← COMPLETE (v2 wins on ≥3 metrics vs v1)
              |
              +-- A126: Port v1 features   ← COMPLETE (telemetry, cost, taxonomy ported)
              |
              +-- A127 / Phase 4: Promote  ← COMPLETE (v2 is now default, v1 archived)
```

✅ **All phases complete.** The ARC v2 migration is finished. v2 is the 
production default agent. v1 remains available via `--agent-version=v1` for 
regression testing and reference.

---

## Related Documents

- `docs/specs/refactorLearning.md` — full analysis and rationale
- `ARCHITECTURE.md` — current architecture reference (to be updated in Phase 4)
- `backlog/masterBacklogTracker.md` — active card tracker
- **HippoCampy B277** — `hippocampy/backlog/B277.md` — basal ganglia extraction card
- **HippoCampy auto-skill spec** — `hippocampy/docs/superpowers/specs/2026-05-26-auto-skill-generation.md` — original design that B277 implements
