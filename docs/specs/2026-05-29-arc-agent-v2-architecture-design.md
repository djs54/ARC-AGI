# ARC Agent v2 — Graph-Driven Workflow Architecture

> Design spec for a rethink of the ARC-AGI agent architecture.
> Replaces the monolithic orchestrator with a layered, graph-driven workflow
> that separates planning, execution, evaluation, and goal management.

## Problem statement

The current ARC agent has three entangled failures:

1. **Goal blindness.** Victory condition stayed "unknown" for all 5 steps of
   the SU15 smoke test. Without a clear goal, the agent cannot evaluate whether
   actions are helping.

2. **No self-correction loop.** The agent repeated ACTION6 five times with
   100% falsification and never tried ACTION7. The planner that chooses the
   action is the same code that evaluates the result — there is no independent
   check.

3. **Monolithic orchestrator.** Planning, execution, evaluation, memory, goal
   management, and telemetry are all in one 9500-line file. No piece can be
   reasoned about, tested, or improved independently.

These are symptoms of the same root cause: the agent lacks a structured
workflow with separation of concerns between planning, execution, evaluation,
and goal management. The world model graph exists in Campy but the agent
barely uses it for decisions.

## Design principles

1. **The graph decides, the LLM advises.** Durable belief state belongs to the
   world model graph, not to LLM context. The LLM generates hypotheses and
   proposes experiments, but the graph holds what is believed, what is
   falsified, and what experiment is worth paying for next.

2. **MCP seam only.** All communication between the ARC agent and Campy goes
   through the MCP stdio adapter. No direct imports. No exceptions.

3. **Separate agents for separate concerns.** Planning, vetting, execution,
   and evaluation are independent modules with clear interfaces. Each reads
   from and writes to the graph. No module grades its own work.

4. **Deterministic where possible, LLM where needed.** Graph queries and
   heuristics handle the common path. LLM reasoning is reserved for
   hypothesis generation, plan generation, and ambiguity resolution.

5. **Parallel prototype.** The new agent plugs into the existing
   `run_single_puzzle.py` entry point alongside the current orchestrator.
   The current agent remains as a fallback until the prototype proves itself.

## Layer architecture

Inspired by the Sandbox ecosystem 7-layer model and Anthropic's building
effective agents patterns.

### Layer 1: Game Harness (existing, unchanged)

The ARC API adapter, `run_single_puzzle.py`, and the runner. Provides the
game environment: available actions, observations after actions, step budget.

### Layer 2: Workflow Orchestrator (new)

A lightweight state machine that dispatches phases and enforces gates.
The orchestrator does NOT reason — it routes.

```
GameWorkflow:
  state: { step, phase, budget, game_id }
  
  loop:
    1. PERCEIVE    → call PerceiveAgent(game_state, graph)
    2. RESOLVE     → call GoalResolver(graph)
    3. PLAN        → call PlanGenerator(goal, graph)
    4. VET         → call PlanVetter(plan, graph)      # pre-action advisory
       if blocked  → goto PLAN with vetter feedback
    5. EXECUTE     → call Executor(approved_plan, game_api)
    6. EVALUATE    → call Evaluator(result, goal, graph) # post-action advisory
       if pivot    → goto RESOLVE
       if continue → goto PERCEIVE
    
  gates:
    - budget_guard: stop if step >= max_steps
    - exploration_gate: block if untested actions exist and all tested actions
      are falsified
    - stall_detector: escalate if 3+ steps with no meaningful progress
```

The orchestrator is a single file, under 500 lines, readable top-to-bottom.
Each phase is a function call that returns a structured result.

### Layer 3: Agent Modules (new)

Each agent is a self-contained module with:
- Input: graph snapshot + phase-specific context
- Output: graph mutations + structured decision
- Optional LLM escalation when graph evidence is insufficient

#### PerceiveAgent

Observes the game state after an action and updates the graph.

- Reads: game observation (grid, reward, terminal status)
- Queries graph: previous GridSnapshot, known GridEntities
- Writes to graph: new GridSnapshot, entity updates, ActionEffect
- LLM escalation: none (purely deterministic)

#### GoalResolver (tiered: heuristic → graph → LLM)

Determines what "winning" means for this game.

- **Tier 1 — Heuristic:** Check structural patterns (color groups, spatial
  layout, symmetry). Cheap. Runs every step.
- **Tier 2 — Graph query:** Query VictoryCondition nodes with INFERRED_FROM
  edges to active Hypotheses. Return the highest-confidence goal.
- **Tier 3 — LLM escalation:** When graph evidence is ambiguous (multiple
  goals with similar confidence, or all goals below threshold), invoke LLM to
  analyze the game state and propose a goal hypothesis. Write the result back
  to the graph.
- Maintains competing goal hypotheses as graph nodes. Promotes/demotes based
  on evidence edges. Confidence can only increase when meaningful progress
  occurs (grounding gate from A117).

#### PlanGenerator

Proposes the next action based on the current goal and graph evidence.

- Queries graph: ActionFact nodes for tested actions and their causal power,
  untested actions, falsified predictions, mechanic priors
- Generates candidates ranked by:
  1. Untested actions (exploration priority)
  2. Actions with graph-backed causal evidence toward the goal
  3. Actions with mechanic prior compatibility
  4. Fallback probe candidates
- LLM escalation: when no graph evidence exists and the action space is large,
  invoke LLM to propose an experiment with a prediction and falsification
  condition.

#### PlanVetter (pre-action advisory gate)

Independently reviews the proposed plan before execution. This is the "second
opinion" that prevents the SU15 failure pattern.

The vetter asks five questions, all answered by graph queries:

1. **Has this action been falsified?** Query ActionFact for contradiction count.
   If falsified 3+ times, block unless it's the only option.
2. **Are untested alternatives available?** Query ActionFact for untested
   actions. If untested alternatives exist and this action has been tried
   2+ times, block.
3. **Does this serve the current goal?** Query the causal path from this action
   to the active VictoryCondition. If no path exists, flag.
4. **Is this a repeat of a failed strategy?** Query ChunkExecution for recent
   failures with the same action family.
5. **Does the mechanic prior support this?** If an ArcMechanic is matched,
   check its ARC_MECHANIC_HAS_ACTION_PATTERN edges.

Returns: `{approved: bool, reason: str, alternative_suggestion: str | null}`

LLM escalation: none. The vetter is purely deterministic graph queries. If you
can't answer the question from the graph, the answer is "insufficient evidence,
proceed with caution."

#### Executor

Carries out the approved action. Thin wrapper around the game API.

- Takes: approved PlanCandidate with action_id, args
- Returns: game observation (grid, reward, terminal, step count)
- No LLM involvement. No graph access.

#### Evaluator (post-action advisory gate)

Judges the result of the action and updates beliefs. This is the adversarial
review that catches unrecognized failures.

- Compares predicted effect (from plan) against actual effect
- Computes terminal grounded score (existing SolveEngine logic)
- Queries graph: goal distance trend, entity movement toward goal
- Updates graph:
  - Confirm or contradict relevant Hypotheses
  - Update ActionFact confidence and value_status
  - Update VictoryCondition evidence
  - Record falsification if prediction was wrong
  - Track class-level falsification counts (A115)
- Returns: `{continue | pivot | terminate, reason, graph_mutations[]}`

LLM escalation: when the effect is ambiguous (neither clearly helpful nor
clearly harmful), invoke LLM to interpret what happened and propose a
hypothesis update.

### Layer 4: Deterministic Tools (existing, refactored)

The existing computation modules, cleaned up and called as services by the
agent modules:

- `SolveEngine` — terminal grounded scoring, graduation scoring
- `GoalHypothesisDetector` — structural goal detection heuristics
- `HybridPatternMatcher` — scene graph similarity
- `GridAnalysis` — grid characteristic extraction

These remain pure functions. No graph access, no LLM calls. They receive data
and return computed results.

### Layer 5: Memory (existing: HippoCampy via MCP)

The world model graph in Campy, accessed exclusively through MCP tools.

#### New ARC-Specific MCP Query Tools

These tools are added to Campy's tool surface (`campy/brain/thalamus/tools/`)
and registered in `TOOL_HANDLERS`. They provide the structured graph queries
the agent modules need:

| MCP Tool | Purpose | Returns |
|---|---|---|
| `arc_get_goal_evidence` | Query VictoryCondition nodes with supporting/contradicting evidence | `{goals: [{type, confidence, supports, contradicts}]}` |
| `arc_get_action_evidence` | Query ActionFact/ActionEffect for a specific action's track record | `{tested, falsified_count, causal_power, value_status}` |
| `arc_get_untested_actions` | List actions that have never been tried in this game | `{untested: [action_id], tested: [action_id]}` |
| `arc_is_hypothesis_falsified` | Check if a hypothesis has been contradicted by evidence | `{falsified: bool, contradiction_count, evidence: [...]}` |
| `arc_get_entity_movement` | Track entity positions relative to goal across steps | `{entities: [{id, moved_toward_goal, distance_delta}]}` |
| `arc_get_mechanic_priors` | Recall mechanics with matching action patterns from past games | `{mechanics: [{name, confidence, action_patterns, effect_patterns}]}` |
| `arc_update_belief` | Write a belief update (confirm/contradict/promote/demote) to the graph | `{status: "ok", mutations: [...]}` |
| `arc_game_context` | Get a compact summary of current game belief state | `{goal, action_summary, hypothesis_count, step, progress_trend}` |

Each tool is a bounded Cypher traversal over the existing ARC graph schema —
no new node types needed. The schema already has `GridEntity`, `ActionFact`,
`ActionEffect`, `Hypothesis`, `VictoryCondition`, `ArcMechanic`, and all the
relationship types.

### Cross-Cutting: Governance

- **Budget guard:** hard step limit enforced by the orchestrator
- **Exploration gate:** cannot repeat a tested action when untested actions
  exist and the tested action has been falsified 2+ times (PlanVetter query)
- **Stall detector:** after 3 consecutive steps with no meaningful progress,
  escalate to LLM reasoning or terminate
- **Falsification tracker:** class-level falsification counts decay action
  confidence across the entire action family, not just individual candidates
- **Telemetry emission:** each phase emits structured events to
  `submission_results_single.live.jsonl` and
  `submission_results_single.world_model.live.jsonl`

## Goal system design

The goal system is the most critical change. Current architecture has no
usable goal until it's too late.

### Goal Lifecycle

```
Game Start
  │
  ├─ Tier 1: Heuristic detection (step 0)
  │    Color groups → color-match hypothesis
  │    Spatial layout → reach-target hypothesis
  │    Pattern structure → pattern-complete hypothesis
  │    Each creates a VictoryCondition node with initial confidence
  │
  ├─ Tier 2: Graph evidence (every step)
  │    Query INFERRED_FROM, CONFIRMS, CONTRADICTS edges
  │    Update confidence based on evidence balance
  │    Promote leading hypothesis, demote contradicted ones
  │    Grounding gate: confidence increases require meaningful progress
  │
  └─ Tier 3: LLM escalation (when needed)
       When top-2 goal confidences are within 0.1 of each other
       When all goals are below 0.3 confidence
       When 3+ steps pass with no goal resolution
       LLM analyzes game state and proposes/refines goal hypothesis
       Result written back to graph as a new VictoryCondition
```

### Goal-Conditioned Planning

Once a goal is resolved (confidence > threshold), all planning is conditioned
on it:
- PlanGenerator ranks actions by causal path to the goal
- PlanVetter checks if proposed action serves the goal
- Evaluator measures progress toward the goal (distance delta, entity movement)

If the goal is falsified (entity reaches "goal" but no reward), the Evaluator
demotes it and triggers a RESOLVE phase to find a new goal.

## Migration strategy

### Phase 1: Parallel Prototype (2-3 weeks)

1. Create `agents/arc4/` directory for the v2 agent
2. Implement the orchestrator state machine (~500 lines)
3. Implement PerceiveAgent, GoalResolver, PlanGenerator as thin modules
   that call existing deterministic tools
4. Implement PlanVetter with hardcoded graph queries (can be mock/stub
   initially if MCP tools aren't ready)
5. Wire into `run_single_puzzle.py` with a `--agent-version=v2` flag
6. Run on SU15 puzzle and compare

### Phase 2: MCP Query Tools (1-2 weeks, parallel with Phase 1)

1. Add `arc_get_goal_evidence`, `arc_get_action_evidence`,
   `arc_get_untested_actions` to Campy's tool surface
2. Add `arc_is_hypothesis_falsified`, `arc_update_belief`
3. Register all in TOOL_HANDLERS
4. Test through MCP stdio adapter
5. Wire into ARC v2 agents via MCPBrainClient

### Phase 3: Validation and Comparison (1 week)

1. Run v1 and v2 on the same set of smoke puzzles
2. Compare: steps to solve, actions tried, goal accuracy, crash rate
3. If v2 matches or exceeds v1, begin migration
4. If not, identify specific gap and iterate

### Phase 4: Migration (2+ weeks)

1. Port remaining v1 features (telemetry, cost tracking, failure taxonomy)
2. Archive v1 orchestrator
3. Update backlog: re-map relevant A-series cards to v2 architecture,
   archive cards that are obsoleted by the new design

## What this obsoletes from the current backlog

| Card | Status under v2 |
|---|---|
| A112 (Crash root-cause capture) | Still needed — move to v2 orchestrator |
| A113 (Force untested exploration) | Replaced by PlanVetter exploration gate |
| A114 (Goal distance fallback) | Replaced by GoalResolver tiered system |
| A115 (Class-level falsification) | Replaced by Evaluator falsification tracking |
| A116 (Lesson→mechanic promotion) | Still needed — MCP tool layer |
| A117 (Grounding gate) | Replaced by GoalResolver grounding gate |
| A073-A078 (World model graph) | Partially realized — the graph schema exists in Campy, this design makes the agent actually use it |
| A086-A090 (Evidence-backed planner) | Replaced by PlanGenerator + PlanVetter |
| A093 (Falsification/quarantine) | Replaced by PlanVetter falsification query |

## Files to create

```
agents/arc4/                        # v2 agent package
agents/arc4/__init__.py
agents/arc4/workflow.py             # Layer 2: orchestrator state machine
agents/arc4/perceive.py             # Layer 3: PerceiveAgent
agents/arc4/goal_resolver.py        # Layer 3: GoalResolver (tiered)
agents/arc4/plan_generator.py       # Layer 3: PlanGenerator
agents/arc4/plan_vetter.py          # Layer 3: PlanVetter (advisory gate)
agents/arc4/executor.py             # Layer 3: Executor
agents/arc4/evaluator.py            # Layer 3: Evaluator (advisory gate)
agents/arc4/graph_queries.py        # MCP query helpers (wraps MCPBrainClient)

# Campy-side (hippocampy repo):
campy/brain/thalamus/tools/arc_queries.py  # New ARC MCP query tools
```

## Non-goals

- Do not tune LLM prompts in this card. Focus on architecture.
- Do not change the game API or harness.
- Do not import Campy internals. MCP seam only.
- Do not remove the v1 agent until v2 is proven.
- Do not add MCP calls to the execute hot path.
- Do not store more raw text when a graph update is enough.
