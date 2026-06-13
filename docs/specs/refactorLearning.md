# ARC Agent v2 — Refactor Learning

> Everything we learned during the analysis phase: the SU15 failure diagnosis,
> graph utilization gaps, brain-region architecture mapping, Anthropic pattern
> review, and Sandbox architecture lessons. This document captures the *why*
> behind the refactor plan.

---

## 1. SU15 Smoke Test Failure Analysis (2026-05-20)

### What happened

The agent ran 5 steps on the SU15 click game. It chose ACTION6 on every step
and never tried ACTION7. Goal distance dropped from 31.5 to 27.0 (step 2),
then went null for steps 3-5. The run ended with `failure_class=crash` and
`exception_type=UnknownCrash`. Victory condition remained "unknown" throughout.

### Artifact evidence

| Artifact | Key finding |
|---|---|
| `submission_results_single.json` | `correct: false`, `steps: 5`, `crash_rate: 1.0`, `exception_type: UnknownCrash` |
| `agent_execution_trace.json` | `exploration_coverage_snapshot: tested=["ACTION6"], untested=["ACTION7"]` at step 3. All planner selections chose ACTION6 via click_probe mode |
| `world_model.live.jsonl` | Step 2: `goal_distance=27.0, meaningful_progress=true`. Steps 3-5: `goal_distance=null, meaningful_progress=false`. All steps: `memory_transfer_state="zero_priors"` |
| `live.jsonl` | Archetype confidence: 0.40 → 0.38 → 0.36 → 0.49 → 0.57. Victory condition: "unknown" on all steps. Confidence increased without progress (ungrounded) |
| `master_timeline.json` | Full perceive → model → plan → act cycles. SideQuests memory calls succeeded but returned no useful priors |

### Six root causes identified

| ID | Root cause | Priority | Code location |
|---|---|---|---|
| RC1 | Crash traceback not captured — only generic "UnknownCrash" message | P0 | `runner.py` line 493, `failure_taxonomy.py` |
| RC2 | ACTION7 never tried — `generate_click_probe_candidates()` hardcodes ACTION6 only | P0 | `world_model_planner.py` line 623: `if "ACTION6" not in available_actions: return`. Orchestrator line 5545: click-probe path only triggers for ACTION6 |
| RC3 | Goal distance drops to null and stays null — proximity scoring zeros out | P1 | `solver.py` line 2990: `proximity_delta = float(effect.get("player_goal_distance_delta", 0.0) or 0.0)` — null becomes 0.0 silently |
| RC4 | 100% falsification rate has no class-level effect — each new coordinate starts fresh | P1 | `world_model_planner.py` line 272: contradiction penalty is per-candidate, not per-action-class |
| RC5 | Lessons written but never promoted to mechanic priors — `zero_priors` on every step | P2 | No promotion pipeline from `upsert_lesson` to `publish_mechanic_summary` |
| RC6 | Archetype confidence increases without progress — ungrounded drift | P1 | `goal_hypothesis.py`: confidence set by structural analysis, no gate against empirical evidence |

---

## 2. Current Architecture Problems

### The monolithic orchestrator

`agents/arc3/orchestrator.py` is 9555 lines. It handles:
- Phase transitions (perceive → model → plan → act → evaluate)
- LLM call construction and parsing
- Memory reads and writes via MCP
- Action selection and coordinate targeting
- Prediction generation and falsification
- Goal hypothesis management
- Telemetry emission
- Error handling and failure classification

No piece can be tested, reasoned about, or improved independently.

### Module sizes

| File | Lines | Responsibility |
|---|---|---|
| `orchestrator.py` | 9555 | Everything |
| `solver.py` | 4492 | Graduation scoring, terminal grounded score, action coverage |
| `runner.py` | 4452 | Step loop, failure handling, result packaging |
| `hypothesis.py` | 1807 | Hypothesis management |
| `world_model.py` | 1408 | World model graph (local, not Campy) |
| `world_model_planner.py` | 818 | Candidate generation and ranking |
| `goal_hypothesis.py` | 350 | Goal detection heuristics |

### The agent doesn't use its own graph

Campy's graph schema has 49 node types and ~45 relationship types. The
ARC-relevant subset includes `GridEntity`, `ActionFact`, `ActionEffect`,
`Hypothesis`, `VictoryCondition`, `ArcMechanic`, `ArcActionPattern`,
`ArcEffectPattern`, `ArcFailureMode`, and `ArcRecoveryPolicy` — with rich
relationship types like `CONFIRMS`, `CONTRADICTS`, `MOVED_BY`, `RESPONDS_TO`,
`CO_MOVES_WITH`, `CAUSES_CHANGE_IN`, `INFERRED_FROM`, and
`ARC_MECHANIC_HAS_ACTION_PATTERN`.

The agent stores observations in Python dicts and dataclasses instead of
querying the graph for beliefs, evidence, and contradictions. Specifically:

1. Does not query for falsified hypotheses (graph has `CONTRADICTS` edges)
2. Does not query for action causal power (`ActionFact` and `MOVED_BY` exist)
3. Does not query for goal evidence (`VictoryCondition` with `INFERRED_FROM`)
4. Does not query for transferred mechanics (`ArcMechanic` with patterns)
5. Uses the graph for telemetry/storage, not as a decision substrate

---

## 3. Anthropic Pattern Review

### Building Effective Agents (Anthropic engineering blog)

**Workflows vs Agents:**
- **Workflows** follow predefined code paths — LLMs and tools are orchestrated
  through predefined code paths. Predictable for well-defined tasks.
- **Agents** dynamically direct their own processes and tool usage. Needed when
  it's "difficult or impossible to predict the required number of steps."

**Key workflow patterns:**
- **Prompt chaining:** Sequential steps with programmatic validation gates
- **Routing:** Classify inputs, direct to specialized handlers
- **Parallelization:** Breaking tasks into independent parallel subtasks
- **Orchestrator-workers:** Central LLM dynamically decomposes and delegates
- **Evaluator-optimizer:** One LLM generates, another evaluates in a loop

**Core advice:** "Add complexity only when it demonstrably improves outcomes."
Start with optimized single LLM calls, progress to workflows, employ agents
only when you can't hardcode a fixed path.

### Claude Code Dynamic Workflows (Opus 4.8)

Dynamic workflows are JavaScript scripts that orchestrate subagents at scale.
Key properties:
- The plan lives in code (a script), not in LLM context
- Intermediate results stay in script variables, not in Claude's context
- The orchestration is repeatable and resumable
- Independent agents can adversarially review each other's findings
- Up to 16 concurrent agents, 1000 total per run

**Critical insight:** "A workflow can have independent agents adversarially
review each other's findings before they're reported, or draft a plan from
several angles and weigh them against each other, so you get a more
trustworthy result than a single pass."

### Claude Code Best Practices

**Relevant patterns for ARC:**
- **Explore first, then plan, then code** — separation of concerns
- **Give Claude a way to verify its work** — the evaluator loop
- **Add an adversarial review step** — "a reviewer running in a fresh context
  sees only the diff and the criteria, not the reasoning that produced the
  change"
- **Writer/Reviewer pattern** — one session writes, another reviews

### Application to ARC

The current ARC agent violates all of these:
- The planner and evaluator are the same code (no adversarial review)
- The plan lives in LLM context, not in durable state
- There is no separation between exploration and exploitation
- No verification step exists between planning and execution

---

## 4. Sandbox Architecture Lessons

### The 7-layer model

The Sandbox ecosystem uses a layered architecture inspired by Gartner-style
enterprise agent platform thinking:

| Layer | Role | ARC equivalent |
|---|---|---|
| 1. Experience & Interaction | Input channels | Game harness / ARC API |
| 2. Agent Orchestration & Control Plane | Durable workflow, routing, gates | Missing — jammed into orchestrator.py |
| 3. Agent Runtime & Harness | Specialized agents with governed tool access | The LLM call, tangled with everything |
| 4. Deterministic Tools & Services | Reusable compute, validators | solver.py, world_model_planner.py |
| 5. Data Products & Memory | Governed data, durable memory | HippoCampy via MCP |
| Cross-cutting | Governance, evaluation, observability | Missing as a separate layer |

### The CopyEdit agent pattern

The Sandbox CopyEdit agent demonstrates clean separation:
```
client → orchestrator route → Harness invoke → agent reasons →
orchestrator post-processing (hallucination check, no-op filter, confidence gate)
```

The post-processing pipeline is an adversarial evaluation layer that runs in
the orchestrator, not inside the agent. The agent proposes; the orchestrator's
post-processing decides if it's good enough.

### Temporal.io integration

The Sandbox uses Temporal.io for durable, retriable workflow execution:
- Feature-flagged: falls back to inline when disabled
- Activities are retried with exponential backoff
- Permanent failures raise `non_retryable=True`
- Background collection merges workflow results asynchronously
- Run state is durable and queryable

### Key takeaway

The orchestrator dispatches and gates. Agents reason. Deterministic services
compute. Post-processing evaluates. These are separate layers with separate
lifecycles.

---

## 5. Graph-Solutions Analysis

### Fit Assessment

**Strong fit.** The ARC agent's top queries are relationship-centric and
multi-hop:

| Query | Hops | Path |
|---|---|---|
| Which actions have causal power toward the goal? | 3-4 | Action → Effect → Entity → Goal |
| Is this hypothesis falsified? | 1-2 | Hypothesis ← CONTRADICTS ← Evidence |
| Which mechanic priors match this game? | 2-3 | ArcMechanic → ActionPattern → EffectPattern |
| What entities moved toward the goal? | 2 | Entity → MOVED_BY → Effect |
| Does this action serve the current goal? | 4-5 | Action → ActionFact → ActionEffect → MOVED_BY ← GridEntity → REQUIRES_ENTITY ← VictoryCondition |

### Model: Labeled Property Graph (confirmed)

Campy uses Kuzu (embedded LPG with Cypher). Correct because:
- Traversal-first operational behavior is the primary need
- Edge properties (confidence, step, weight) are essential
- No interoperability or ontology requirement
- Bounded local traversals, not global inference

### Graph Risks Identified

**Risk 1: Supernode at VictoryCondition.** If every piece of evidence connects
to VictoryCondition via INFERRED_FROM/CONFIRMS/CONTRADICTS, goal nodes become
dense hubs. Mitigated by scoping to `task_id`.

**Risk 2: Catch-all write tool.** A single `arc_update_belief` tool that
handles all mutations is an anti-pattern. Split into purpose-specific writes:
`arc_confirm_hypothesis`, `arc_contradict_hypothesis`,
`arc_record_action_effect`, `arc_update_goal_confidence`.

**Risk 3: Missing causal path query.** PlanVetter question 3 ("Does this serve
the goal?") requires a 4-5 hop traversal. Needs a dedicated
`arc_get_causal_path` MCP tool that does this server-side.

**Risk 4: Entity identity across steps.** GridEntity nodes have
`last_updated_step` but identity across steps is tricky. The OBSERVED_IN
relationship connects entities to snapshots, but MOVED_BY and CO_MOVES_WITH
need to bridge across step-scoped entities. Must clarify if entities are
stable or step-scoped.

### Anti-patterns to avoid

- Don't do full-graph scans in the step loop (Anti-pattern 3)
- Don't model every observation as a node (Anti-pattern 4)
- Don't assume all graph queries are cheap (Anti-pattern 2: bound traversals)
- Don't hide business identity behind opaque IDs (Anti-pattern 6)

---

## 6. Brain Architecture Mapping

### How the human brain solves a puzzle

```
Sensory Input (eyes see the grid)
     |
     v
Sensory Cortex --> "I see colored blocks in a pattern"
     |
     v
Hippocampus --> "This reminds me of that puzzle I solved before"
     |              (episodic memory, pattern matching)
     v
Temporal Lobe --> "The rule seems to be: click matching colors"
     |              (semantic memory, concept formation)
     v
Prefrontal Cortex --> "My plan: click the blue block next to the red one"
     |                   (planning, goal-directed behavior)
     v
Basal Ganglia --> "Wait - last time I clicked blue, nothing happened"
     |              (action selection, reward prediction error)
     v
Thalamus --> Routes the decision to motor cortex
     |          (relay, gating, attention)
     v
Motor Cortex --> Execute the click
     |
     v
Back to Sensory Cortex --> "What changed?"
```

### Mapping to Campy brain regions + ARC agent phases

| Brain Region | Campy Module | ARC v2 Agent | Role |
|---|---|---|---|
| **Sensory Cortex** | `campy/brain/sensory_cortex/` | **PerceiveAgent** | Raw input to structured representation. Capture, ingest, extract entities |
| **Hippocampus** | `campy/brain/hippocampus/` | **Graph Store** (memory itself) | Episodic + spatial memory. GridSnapshot, ActionEffect, entity positions. "Where was I? What happened before?" |
| **Temporal Lobe** | `campy/brain/temporal_lobe/` | **GoalResolver + Evaluator** | Semantic memory, concept formation, pattern recognition. "What type of game is this? What is the goal?" The temporal lobe's loop pipeline (NER, gist, schema, pattern, retrieval, arbitration) mirrors the GoalResolver's tiered processing |
| **Prefrontal Cortex** | *(not in Campy — the LLM)* | **PlanGenerator** | Executive function, planning, hypothesis generation. The expensive reasoning step |
| **Basal Ganglia** | *(not in Campy yet — missing piece)* | **PlanVetter** | Action selection, reward prediction, habit formation. "Has this action been rewarded? Should I repeat or try something new?" |
| **Thalamus** | `campy/brain/thalamus/` | **Workflow Orchestrator** | Relay and gating. Routes signals, filters what reaches higher processing. Doesn't think — dispatches |
| **Brainstem** | `campy/brain/brainstem/` | **Cross-cutting governance** | Autonomic regulation — config, activity logging, phase management. Budget guards, stall detectors |

### What this reveals

**The spec is missing the Basal Ganglia.** The human brain has a dedicated
structure for action selection and reward prediction error. It does exactly
what the PlanVetter needs:

1. **Dopamine signal (reward prediction error):** "I predicted clicking blue
   would score. It didn't. Reduce confidence in clicking blue." This is
   falsification tracking.
2. **Go/No-Go pathway:** "I want to click blue again, but the No-Go pathway
   fires because it's been falsified 3 times." This is the vetting gate.
3. **Habit vs. exploration:** "I keep doing the same thing. Switch to
   exploration mode." This is the untested-action gate.

**Recommendation:** Add a `basal_ganglia` module to Campy
(`campy/brain/basal_ganglia/`) that provides:
- Reward prediction error calculation
- Go/No-Go gating based on accumulated evidence
- Exploration vs exploitation balance

**The spec also underweights the Temporal Lobe's role.** The temporal lobe is
not just goal resolution — it's where semantic memory lives. "This is a maze
game" is a temporal lobe classification. The GoalResolver should use Campy's
existing Concept nodes and relationship edges (CO_OCCURS_WITH, REQUIRES,
ENABLES) to classify game archetypes and infer goals from them.

### Updated MCP tool surface (brain-region aligned)

| MCP Tool | Brain Region | Purpose |
|---|---|---|
| `arc_perceive_state` | Sensory Cortex | Ingest game observation, extract entities, create GridSnapshot |
| `arc_get_game_context` | Hippocampus | Compact episodic summary: "Where am I? What happened?" |
| `arc_get_goal_evidence` | Temporal Lobe | Query VictoryCondition + Concept archetype evidence |
| `arc_classify_game_archetype` | Temporal Lobe | Match current game to known archetypes via Concept graph |
| `arc_get_action_evidence` | Hippocampus | Action track record from ActionFact/ActionEffect history |
| `arc_get_untested_actions` | Hippocampus | What haven't we tried? |
| `arc_check_action_gate` | Basal Ganglia | Go/No-Go decision: falsification, reward prediction error, exploration need |
| `arc_get_causal_path` | Hippocampus | Bounded traversal: does this action lead toward the goal? |
| `arc_record_action_effect` | Hippocampus | Write: what happened when we took this action |
| `arc_confirm_hypothesis` | Temporal Lobe | Write: evidence supports this hypothesis |
| `arc_contradict_hypothesis` | Temporal Lobe | Write: evidence contradicts this hypothesis |
| `arc_update_goal_confidence` | Temporal Lobe | Write: update goal confidence with grounding gate |
| `arc_get_mechanic_priors` | Temporal Lobe | Cross-game transfer: matching mechanics from past games |
| `arc_record_reward_prediction_error` | Basal Ganglia | Write: predicted vs actual reward delta |

---

## 7. Backlog Impact

### Cards replaced by v2 architecture

| Card | v1 Purpose | v2 Status |
|---|---|---|
| A113 | Force untested action exploration | Replaced by PlanVetter exploration gate + `arc_check_action_gate` |
| A114 | Goal distance fallback | Replaced by GoalResolver tiered system |
| A115 | Class-level falsification decay | Replaced by Evaluator + Basal Ganglia reward prediction error |
| A117 | Grounding gate on confidence | Replaced by GoalResolver grounding gate |
| A073-A078 | World model graph series | Partially realized — graph schema exists, v2 makes the agent use it |
| A086-A090 | Evidence-backed planner series | Replaced by PlanGenerator + PlanVetter |
| A093 | Fast falsification/quarantine | Replaced by `arc_check_action_gate` |

### Cards still needed

| Card | Reason |
|---|---|
| A112 | Crash root-cause capture — still needed in v2 orchestrator |
| A116 | Lesson-to-mechanic promotion — MCP tool layer, orthogonal to architecture |

### New work created by v2

The v2 architecture creates new work not in the current backlog:
- ~~Basal ganglia module in Campy~~ → **B277 (complete)** — 857 lines, 26
  tests passing. Created `campy/brain/basal_ganglia/` with 6 modules:
  `frustration_clusters.py`, `procedure_synthesis.py`,
  `procedure_maturity.py`, `action_selector.py`, `reward_predictor.py`,
  `exploration_policy.py`. Campy now has 6 brain regions.
- 14 new ARC MCP query tools (in `campy/brain/thalamus/tools/arc_queries.py`)
  — these sit on top of B277's basal ganglia + existing hippocampus/temporal
  lobe modules. Needs its own card after B277 lands.
- `agents/arc4/` module structure (ARC_AGI side)
- Workflow orchestrator state machine (ARC_AGI side)

### B277 assessment

B277 was independently created by the Campy agent and aligns with our v2
architecture. Key findings from review:

**What B277 provides that we need:**
- `action_selector.check_action_gate()` — Go/No-Go decision pattern
- `reward_predictor.record_reward_prediction_error()` — RPE tracking pattern
- `exploration_policy.should_explore()` — explore/exploit balance

**What B277 does NOT cover (follow-up needed):**
- ARC-specific action gating (by ActionFact contradiction counts, not Procedure
  vector search)
- ARC-specific RPE targets (ActionFact/ArcWorldModelStep, not Plan nodes)
- ARC-specific exploration (action-ID-based untested checks, not vector search)

**Conclusion:** B277 builds the right foundation. The ARC-specific adaptations
belong in a follow-up MCP tool card that wraps B277's modules with
ARC-specific graph queries. This matches our Phase 2 plan.
