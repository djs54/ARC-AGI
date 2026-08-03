# ARC-AGI-3 Agent Architecture

> **Scope:** This document covers the architecture of the ARC-AGI-3 agent (`agents/arc3/`).
> For the SideQuests Brain system design (Kùzu schema, Loop steps, MCP tools), see
> [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md).

The ARC-AGI-3 agent is the first structured proof-of-concept for SideQuests augmenting a real
AI benchmark. It implements a two-phase cognitive loop that mirrors how humans approach an
unfamiliar game. **No SideQuests schema changes are involved** — the agent consumes SideQuests
through the existing MCP tool surface only.

---

## Phase 1 — Explore (B88: Hypothesis Engine)

**Goal:** Build a functional model of the game's mechanics *before* attempting to solve it.

```
Perceive → Hypothesize → Plan → Act → Evaluate
```

| Component | Class | What it does |
|---|---|---|
| State Transition Graph | `StateGraph` | In-memory directed graph: nodes = grid states, edges = actions taken. Ephemeral — reset on retry. |
| Invariant Detection | `InvariantDetector` | Finds rows/regions that never change (walls, HUD) vs. dynamic regions (player, enemies). |
| Hypothesis Tracking | `HypothesisManager` | Generates and updates action-semantic hypotheses. Bayesian confidence update per observation. Prunes confidence < 0.2; confirms > 0.8. |
| Action Facts | `ActionFact` | Compact operator summaries extracted from repeated transition evidence (deterministic_effect, blocked, loop, no_op). |
| Path Hypotheses | `PathHypothesis` | Short action-sequence hypotheses about what multi-step paths achieve. |

**Explore outputs** consumed by Phase 2: `action_facts`, `path_hypotheses`, invariant regions
(HUD rows, static rows), state graph (all visited hashes + transitions), energy estimate from HUD.

---

## Phase 2 — Solve (B95: Solve Engine)

**Goal:** Given what the agent learned about the game, formulate and execute a goal-directed
strategy to WIN.

```
Perceive → Hypothesize → Solve → Plan → Act → Evaluate
```

| Component | Class | What it does |
|---|---|---|
| Archetype Classification | `ArchetypeClassifier` | Classifies game into RACE / SPACE / CHASE / DISPLACE. Algorithmic signals + `analogical_search` votes from structurally similar past games. Locks at confidence ≥ 0.65, stays sticky across steps. |
| Object Role Assignment | `ObjectRoleMapper` | Assigns PLAYER / ENEMY / GOAL / WALL / COLLECTIBLE / EXIT roles to color groups. Uses `InvariantDetector` output + transition diffs. |
| Victory Condition Hypothesis | `VictoryHypothesizer` | Identifies the win condition (REACH_GOAL / COLLECT_ALL / SURVIVE / SCORE_THRESHOLD / ELIMINATE). Uses `recall_plans` + `recall_relevant_lessons`, then one LLM call. Called once when archetype locks; re-called only on dissonance. |
| Dissonance Detection | `DissonanceDetector` | Monitors chunk progress. On stall (N zero-progress steps), calls `report_outcome(valence=-0.7)` to encode the failed prediction into the SideQuests graph. Triggers re-hypothesis. |
| Plan Chunking | `PlanChunker` | Decomposes victory condition into macro-action sequences. Primary path: **BFS on StateGraph** (not LLM) for known states — O(V+E), exact. Fallback: directional heuristic from object role positions. Registers each chunk via `register_plan`. |

**Solve state** (sticky across steps, reset between games):

| State | Reset on retry? | Reset on new game? |
|---|---|---|
| Archetype + confidence | No (cross-attempt) | Yes |
| Object role map | No (cross-attempt) | Yes |
| Victory condition | No (cross-attempt) | Yes |
| Active chunk + chunk plan_id | Yes | Yes |

---

## Cognitive Architecture: SideQuests as the Cognitive Substrate

The Solve phase maps directly onto SideQuests' existing architecture. Both SideQuests and the
human brain implement the same Bayesian/Hebbian update loop — the alignment is structural,
not accidental.

| Cognitive Step (Human) | Brain Mechanism | SideQuests Equivalent |
|---|---|---|
| Visual Archetyping | Centroid matching against long-term archetypes | `GistClass` centroids + `analogical_search` |
| System Image (Object Roles) | Schema type assignment before semantic work | schema.org type mapping + `current_truth` |
| Inverted Pyramid (Goal-First) | Retrieve goal before planning steps | `recall_plans` → victory template → `register_plan` |
| Cognitive Dissonance | Failed prediction → strategy pivot | `report_outcome(valence=-0.7)` → Amygdala Reflex |
| Chunking (Mental Load) | Macro-action grouping into single objects | `register_plan` steps + NEXT_STEP graph edges |

### Key Design Principles

**1. BFS for navigation, not LLM.**
`PlanChunker` uses BFS on the in-memory `StateGraph` to find the shortest action path through
already-explored states. This is a graph algorithm (O(V+E)), not a language model problem.
The LLM's one job in the Solve phase is the high-level victory condition hypothesis.

**2. Meta-plan pattern.**
Each plan chunk is registered via `register_plan` as a SideQuests Plan node. This preserves
the full causal chain in the Plan graph and makes `recall_plans` return coherent strategies
across games. Chunk-level valence from `report_outcome` propagates through `OUTCOME_SIGNAL`
edges to related Concept nodes, building a corpus of cross-game execution memory.

**3. Centroid proxy via `analogical_search`.**
`ArchetypeClassifier` uses `analogical_search` on the observation summary string to find
structurally similar past games. The returned Plan nodes carry the archetype and victory
condition that worked — no new Kùzu schema needed. The existing embedding layer is the
centroid proxy.

---

## File Map

```
agents/arc3/
├── arcAgent_Architecture.md   # ← this file
├── hypothesis.py              # Phase 1: StateGraph, InvariantDetector, HypothesisManager (B88)
├── solver.py                  # Phase 2: ArchetypeClassifier, ObjectRoleMapper,
│                              #          VictoryHypothesizer, DissonanceDetector,
│                              #          PlanChunker, SolveEngine (B95)
├── orchestrator.py            # Full loop: Perceive → Hypothesize → Solve → Plan → Act → Evaluate
├── runner.py                  # Episode runner wiring orchestrator to ARC-AGI-3 environment
└── api_knowledge.py           # Static domain knowledge chunks injected at game start

benchmarks/arc3/
├── README.md                  # Benchmark setup, puzzle sources, offline bundle
├── harness.py                 # Baseline vs SideQuests-augmented A/B runner
├── adapter.py                 # Episode normalization bridge
├── model_eval.py              # Model evaluation + prompt budget metrics (B89)
└── state_serializer.py        # State-to-text serialization for causal memory
```

---

## SideQuests Tools Used by the Agent

| Tool | Phase | Purpose |
|---|---|---|
| `notify_turn` | Perceive | Ingest puzzle structure into SideQuests memory |
| `current_truth` | Perceive | Retrieve relevant memories before each puzzle |
| `recall_relevant_lessons` | Perceive + Solve | Lessons from past games |
| `analogical_search` | Perceive + Solve (Archetype) | Cross-game structural similarity |
| `register_plan` | Plan + Solve (Chunking) | Declare strategy; get Amygdala Reflex warnings |
| `recall_plans` | Plan + Solve (Victory) | Past successful plan goals as victory templates |
| `report_outcome` | Evaluate + Solve (Dissonance) | Encode success/failure valence into graph |
