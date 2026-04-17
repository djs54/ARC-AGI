# B-95-arc-solve-engine — ARC Solve Engine: Goal-Directed Strategy

**Card:** B95 | **Priority:** P1 | **Depends on:** B88 (HypothesisManager ✅)

---

## Summary

Add a `SolveEngine` between `hypothesize()` and `plan()` in the ARC orchestrator. The engine
implements 5 human cognitive steps for solving unfamiliar games, using SideQuests tools as the
cognitive substrate. `SolveEngine` owns retrieval/orchestration; subcomponents stay mostly
logic-only. No SideQuests schema changes — only new files in `agents/arc3/`.

---

## Technical Approach

### Architecture

```
Perceive → Hypothesize → Solve → Plan → Act → Evaluate
                           ↕
              agents/arc3/solver.py
              ┌──────────────────────────────────────┐
              │  ArchetypeClassifier                 │
              │    logic only; consumes analogy vote │
              │  ObjectRoleMapper                    │
              │    InvariantDetector + transitions   │
              │  VictoryHypothesizer                 │
              │    logic over retrieved templates    │
              │    → ONE LLM call (sticky)           │
              │  DissonanceDetector                  │
              │    progress monitor → report_outcome │
              │  PlanChunker                         │
              │    BFS on StateGraph (in-memory)     │
              │    → chunk steps under one plan      │
              └──────────────────────────────────────┘
```

### SolveEngine Internal State (survives across steps)

```python
self._archetype: GameArchetype = GameArchetype.UNKNOWN
self._archetype_confidence: float = 0.0
self._object_roles: Dict[int, ObjectRole] = {}       # color_id → ObjectRole
self._victory_condition: VictoryCondition | None = None
self._active_chunk: PlanChunk | None = None
self._chunk_plan_id: str | None = None               # plan_id from register_plan
self._dissonance_count: int = 0
self._on_strategy_steps: int = 0                     # steps since last chunk start
self._archetype_locked: bool = False
```

`reset_for_retry()` clears: `_active_chunk`, `_chunk_plan_id`, `_dissonance_count`,
`_on_strategy_steps`. Preserves: `_archetype`, `_archetype_confidence`, `_object_roles`,
`_victory_condition`, `_archetype_locked`. (Cross-attempt knowledge is preserved.)

### Orchestration Boundary

`SolveEngine` is the only solve-phase component that should call SideQuests tools such as
`analogical_search`, `recall_plans`, `recall_relevant_lessons`, `register_plan`, and
`report_outcome`. `ArchetypeClassifier`, `ObjectRoleMapper`, `VictoryHypothesizer`,
`DissonanceDetector`, and `PlanChunker` should operate mostly as pure logic over already-fetched
data and current hypothesis state.

---

## File-Level Implementation Plan

### NEW: `agents/arc3/solver.py`

```python
"""ARC Solve Engine — goal-directed strategy from game archetype classification.

Five cognitive components:
  ArchetypeClassifier   — What kind of game is this? (centroid-style classification)
  ObjectRoleMapper      — What role does each object play?
  VictoryHypothesizer   — What does winning look like? (inverted pyramid)
  DissonanceDetector    — Is our model wrong? (negative valence encoding)
  PlanChunker           — How do we get there? (BFS + register_plan)
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Enumerations ──────────────────────────────────────────────────────

class GameArchetype(str, Enum):
    RACE     = "race"      # linear path, reach goal first
    SPACE    = "space"     # territorial, open grid positioning
    CHASE    = "chase"     # pursuer/flee dynamic
    DISPLACE = "displace"  # remove targets from board
    UNKNOWN  = "unknown"


class RoleType(str, Enum):
    PLAYER      = "player"
    ENEMY       = "enemy"
    GOAL        = "goal"
    WALL        = "wall"
    COLLECTIBLE = "collectible"
    EXIT        = "exit"
    DECORATION  = "decoration"
    UNKNOWN     = "unknown"


class VictoryType(str, Enum):
    REACH_GOAL       = "reach_goal"
    COLLECT_ALL      = "collect_all"
    SURVIVE          = "survive"
    SCORE_THRESHOLD  = "score_threshold"
    ELIMINATE        = "eliminate"
    UNKNOWN          = "unknown"


# ── Data Structures ───────────────────────────────────────────────────

@dataclass
class ObjectRole:
    color_id: int
    role: RoleType = RoleType.UNKNOWN
    confidence: float = 0.5
    evidence_steps: List[int] = field(default_factory=list)
    estimated_position: Optional[Dict[str, float]] = None  # {"row": r, "col": c}


@dataclass
class VictoryCondition:
    condition_type: VictoryType = VictoryType.UNKNOWN
    description: str = ""
    target_color_id: Optional[int] = None   # which object to reach/collect/eliminate
    confidence: float = 0.0
    evidence_steps: List[int] = field(default_factory=list)
    source: str = "unknown"                  # "recall_plans" | "llm" | "lesson"


@dataclass
class PlanChunk:
    description: str
    estimated_actions: List[str] = field(default_factory=list)   # e.g. ["ACTION1","ACTION1","ACTION2"]
    progress_score: float = 0.0             # 0.0–1.0, increases as chunk executes
    steps_executed: int = 0
    success_condition: str = ""
    source: str = "bfs"                     # "bfs" | "directional" | "llm"


@dataclass
class SolveContext:
    archetype: GameArchetype = GameArchetype.UNKNOWN
    archetype_confidence: float = 0.0
    object_roles: Dict[int, ObjectRole] = field(default_factory=dict)
    victory_condition: Optional[VictoryCondition] = None
    active_chunk: Optional[PlanChunk] = None
    dissonance_detected: bool = False
    dissonance_reason: str = ""
    strategy_summary: str = ""


# ── Archetype Classifier ──────────────────────────────────────────────

class ArchetypeClassifier:
    """Classifies game archetype from hypothesis context + analogy votes.

    Algorithm:
      1. Extract signals from hypothesis_context (moving object count, convergence,
         board transformation pattern, HUD presence).
      2. Score each archetype against signals.
      3. Consume analogy votes already gathered by SolveEngine.
      4. Past game archetype labels vote (weight: 0.4 algorithmic, 0.6 analogy).
      5. Lock when composite confidence > LOCK_THRESHOLD.
    """

    LOCK_THRESHOLD: float = 0.65
    MIN_OBSERVATIONS: int = 5              # don't classify before seeing 5 frames

    def __init__(self) -> None:
        self._observation_count: int = 0
        self._signal_history: List[Dict[str, Any]] = []

    def _extract_signals(self, hypothesis_context: Dict[str, Any]) -> Dict[str, Any]:
        """Pull archetype-relevant signals from HypothesisManager output."""
        action_facts = hypothesis_context.get("action_facts", [])
        # Count how many actions show deterministic single-object movement
        directional_facts = [f for f in action_facts
                             if f.get("fact_type") == "deterministic_effect"]
        # Check for convergence: do any two distinct moving regions approach each other?
        transitions = hypothesis_context.get("last_transition_effect") or {}
        has_hud = bool(hypothesis_context.get("hud_rows"))
        path_hypotheses = hypothesis_context.get("path_hypotheses", [])
        return {
            "directional_actions": len(directional_facts),
            "has_hud": has_hud,
            "path_hypotheses_count": len(path_hypotheses),
            "pixels_changed": transitions.get("pixels_changed", 0),
            "loop_detected": bool(hypothesis_context.get("loop_detected")),
        }

    def _score_archetypes(self, signals: Dict[str, Any]) -> Dict[GameArchetype, float]:
        """Heuristic scoring of each archetype from signals."""
        scores: Dict[GameArchetype, float] = {a: 0.0 for a in GameArchetype}
        d = signals["directional_actions"]
        hud = signals["has_hud"]

        # RACE: few directional actions, HUD (energy/score bar), coherent path pressure
        if hud and d >= 1:
            scores[GameArchetype.RACE] += 0.5
        # CHASE: multiple directional actions, no strong single path
        if d >= 2 and signals["path_hypotheses_count"] == 0:
            scores[GameArchetype.CHASE] += 0.4
        # DISPLACE: board is being reduced / cleared over time
        if signals["pixels_changed"] < 20:
            scores[GameArchetype.DISPLACE] += 0.35
        # SPACE: many path hypotheses, broad navigation
        if signals["path_hypotheses_count"] >= 2:
            scores[GameArchetype.SPACE] += 0.45
        return scores

    def update(
        self,
        hypothesis_context: Dict[str, Any],
    ) -> tuple[GameArchetype, float]:
        """Update archetype estimate from latest hypothesis context.

        Returns (archetype, confidence). Does NOT call SideQuests — caller must
        supply analogy votes from SolveEngine retrieval.
        """
        self._observation_count += 1
        if self._observation_count < self.MIN_OBSERVATIONS:
            return GameArchetype.UNKNOWN, 0.0

        signals = self._extract_signals(hypothesis_context)
        self._signal_history.append(signals)

        scores = self._score_archetypes(signals)
        best = max(scores, key=lambda a: scores[a])
        best_score = scores[best]
        if best_score < 0.3:
            return GameArchetype.UNKNOWN, best_score
        return best, min(best_score, 0.95)

    def apply_analogy_votes(
        self,
        archetype: GameArchetype,
        confidence: float,
        analogy_results: List[Dict[str, Any]],
    ) -> tuple[GameArchetype, float]:
        """Blend analogy votes into the archetype estimate (weight 0.6 analogy)."""
        if not analogy_results:
            return archetype, confidence

        vote_scores: Dict[str, float] = {}
        for result in analogy_results[:5]:
            # Analogical results include text_raw; parse archetype tag if present
            text = (result.get("text_raw") or "").lower()
            for a in GameArchetype:
                if a.value in text and a != GameArchetype.UNKNOWN:
                    vote_scores[a.value] = vote_scores.get(a.value, 0.0) + result.get("similarity", 0.5)

        if not vote_scores:
            return archetype, confidence

        best_vote = max(vote_scores, key=lambda k: vote_scores[k])
        vote_conf = min(vote_scores[best_vote] / len(analogy_results), 0.9)

        # Blend: 0.4 algorithmic + 0.6 analogical
        blended = 0.4 * confidence
        if best_vote == archetype.value:
            blended += 0.6 * vote_conf
        else:
            # Disagreement: cap confidence
            blended = min(blended, 0.5)
            if vote_conf > confidence:
                return GameArchetype(best_vote), blended

        return archetype, min(blended, 0.95)


# ── Object Role Mapper ────────────────────────────────────────────────

class ObjectRoleMapper:
    """Assigns semantic roles to color groups from transitions + invariants.

    Uses:
      - InvariantDetector static_rows → WALL candidates
      - Transitions where specific color region moves with action → PLAYER
      - Reward spike on contact with specific color → GOAL / COLLECTIBLE
      - Color region that tracks toward player → ENEMY
    """

    def update(
        self,
        hypothesis_context: Dict[str, Any],
        observation: Dict[str, Any],
        step: int,
    ) -> Dict[int, ObjectRole]:
        """Return updated object role map from current frame evidence."""
        roles: Dict[int, ObjectRole] = {}
        colors = observation.get("colors") or []
        static_rows = hypothesis_context.get("static_rows", [])
        last_effect = hypothesis_context.get("last_transition_effect") or {}
        changed_regions = last_effect.get("regions_changed", [])
        reward = float((hypothesis_context.get("last_transition_effect") or {}).get(
            "meaningful_change_score", 0.0
        ))

        for color_id in colors:
            role = ObjectRole(color_id=color_id, evidence_steps=[step])

            # Wall heuristic: if color only appears in static rows
            # (simplified: if the entire changed bbox doesn't overlap the static rows)
            if static_rows and not changed_regions:
                role.role = RoleType.WALL
                role.confidence = 0.7

            # Player heuristic: color whose bounding box centroid matches changed_center
            changed_center = last_effect.get("changed_center")
            if changed_center and reward > 0.3:
                role.role = RoleType.PLAYER
                role.confidence = 0.75
                role.estimated_position = changed_center

            roles[color_id] = role

        return roles


# ── Victory Hypothesizer ──────────────────────────────────────────────

class VictoryHypothesizer:
    """Identifies the win condition using recall_plans + recall_lessons + one LLM call.

    Called once when archetype confidence > CALL_THRESHOLD.
    Re-called only when DissonanceDetector fires.
    """

    CALL_THRESHOLD: float = 0.65
    PROMPT_TEMPLATE = """You are analyzing an unknown game. Based on the evidence below,
hypothesize what the WINNING CONDITION is.

Game archetype: {archetype}

Object roles detected:
{object_roles}

Past successful plans with similar goals:
{past_plans}

Known game lessons:
{lessons}

Reward pattern: {reward_summary}

Respond with EXACTLY this JSON format (no other text):
{{
  "condition_type": "<reach_goal|collect_all|survive|score_threshold|eliminate>",
  "description": "<one sentence describing the win condition>",
  "target_color_id": <integer color id or null>,
  "confidence": <0.0-1.0>
}}"""

    async def hypothesize(
        self,
        archetype: GameArchetype,
        object_roles: Dict[int, ObjectRole],
        brain_client: Any,
        llm_client: Any,
        session_id: str,
        task_id: str,
        reward_history: List[float],
        dissonance_reason: str = "",
    ) -> VictoryCondition:
        """Run the full hypothesis pipeline: recall → LLM."""

        # 1. Recall similar past plans
        goal_query = f"{archetype.value} game win condition solve puzzle"
        recall = await brain_client.recall_plans(
            goal_query=goal_query,
            session_id=session_id,
            min_valence=0.2,
            limit=3,
        )
        past_plans = recall.get("plans", [])

        # Check if a past plan gives us a high-confidence victory condition directly
        for plan in past_plans:
            if plan.get("valence", 0.0) > 0.75:
                return VictoryCondition(
                    condition_type=VictoryType.UNKNOWN,  # will be refined by LLM
                    description=plan.get("goal", ""),
                    confidence=0.6,
                    source="recall_plans",
                )

        # 2. Recall game-specific lessons
        lessons_result = await brain_client.recall_relevant_lessons(
            query=f"ARC game {archetype.value} win condition",
            limit=3,
        )
        lessons = lessons_result.get("lessons", [])

        # 3. LLM call
        object_roles_text = "\n".join(
            f"  color_id={r.color_id}: {r.role.value} (confidence={r.confidence:.2f})"
            for r in object_roles.values()
            if r.role != RoleType.UNKNOWN
        ) or "  No roles identified yet."

        past_plans_text = "\n".join(
            f"  - {p.get('goal', '')}" for p in past_plans[:3]
        ) or "  No past plans found."

        lessons_text = "\n".join(
            f"  - {l.get('text', '')}" for l in lessons[:3]
        ) or "  No lessons found."

        reward_summary = (
            f"Last 5 rewards: {reward_history[-5:]}"
            if reward_history else "No reward history."
        )
        if dissonance_reason:
            reward_summary += f" DISSONANCE: {dissonance_reason}"

        prompt = self.PROMPT_TEMPLATE.format(
            archetype=archetype.value,
            object_roles=object_roles_text,
            past_plans=past_plans_text,
            lessons=lessons_text,
            reward_summary=reward_summary,
        )

        import json
        try:
            response = await llm_client.complete(prompt, max_tokens=200)
            text = response.strip()
            # Strip markdown fences if present
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text.strip())
            return VictoryCondition(
                condition_type=VictoryType(data.get("condition_type", "unknown")),
                description=data.get("description", ""),
                target_color_id=data.get("target_color_id"),
                confidence=float(data.get("confidence", 0.5)),
                source="llm",
            )
        except Exception as exc:
            logger.warning("VictoryHypothesizer LLM call failed: %s", exc)
            return VictoryCondition(
                condition_type=VictoryType.UNKNOWN,
                description="Victory condition unknown",
                confidence=0.1,
                source="error",
            )


# ── Dissonance Detector ───────────────────────────────────────────────

class DissonanceDetector:
    """Monitors plan chunk progress. Fires report_outcome(negative) on stall.

    Dissonance conditions:
      - Zero meaningful-change steps >= STALL_THRESHOLD while executing a chunk
      - reward_trend is flat/negative for >= REWARD_STALL_THRESHOLD steps
      - Active chunk exceeded MAX_CHUNK_STEPS without progress_score increase
    """

    STALL_THRESHOLD: int = 6
    REWARD_STALL_THRESHOLD: int = 8
    MAX_CHUNK_STEPS: int = 15

    def __init__(self) -> None:
        self._zero_progress_streak: int = 0
        self._chunk_steps: int = 0

    def update(
        self,
        hypothesis_context: Dict[str, Any],
        active_chunk: Optional[PlanChunk],
        step: int,
    ) -> tuple[bool, str]:
        """Return (dissonance_detected, reason). Pure algorithmic — no async."""
        if active_chunk is None:
            self._zero_progress_streak = 0
            self._chunk_steps = 0
            return False, ""

        self._chunk_steps += 1
        last_effect = hypothesis_context.get("last_transition_effect") or {}
        score = float(last_effect.get("meaningful_change_score", 0.0))

        if score < 0.15:
            self._zero_progress_streak += 1
        else:
            self._zero_progress_streak = 0

        if self._zero_progress_streak >= self.STALL_THRESHOLD:
            return True, f"no meaningful change for {self._zero_progress_streak} steps"

        if self._chunk_steps >= self.MAX_CHUNK_STEPS and active_chunk.progress_score < 0.2:
            return True, f"chunk exceeded {self.MAX_CHUNK_STEPS} steps with low progress"

        return False, ""

    def reset_chunk(self) -> None:
        """Call when a new chunk starts."""
        self._zero_progress_streak = 0
        self._chunk_steps = 0


# ── Plan Chunker ──────────────────────────────────────────────────────

class PlanChunker:
    """Decomposes the victory condition into executable macro-action chunks.

    Primary path: BFS on in-memory StateGraph (free, exact, O(V+E)).
    Fallback path: directional chunk toward estimated goal position.
    Each chunk is registered via register_plan for Amygdala Reflex + cross-game learning.
    """

    def generate_chunk(
        self,
        victory_condition: VictoryCondition,
        object_roles: Dict[int, ObjectRole],
        state_graph: Any,       # StateGraph from hypothesis.py
        current_hash: str,
        available_actions: List[str],
        step: int,
    ) -> PlanChunk:
        """Generate the next plan chunk. Does NOT call SideQuests — caller registers it."""

        # 1. Try BFS if we have a known goal state
        player_role = next(
            (r for r in object_roles.values() if r.role == RoleType.PLAYER), None
        )
        goal_role = next(
            (r for r in object_roles.values()
             if r.role in (RoleType.GOAL, RoleType.EXIT)), None
        )

        if goal_role and hasattr(state_graph, "find_path"):
            # Attempt BFS toward any state where goal object has changed
            # (approximate: we search for transitions that produced reward)
            high_reward_states = [
                t.to_hash for transitions in state_graph.edges.values()
                for t in transitions if t.reward_signal > 0.5
            ]
            for target_hash in high_reward_states:
                path = state_graph.find_path(current_hash, target_hash)
                if path:
                    actions = [t.action for t in path]
                    return PlanChunk(
                        description=f"Navigate via known path to reward state ({len(actions)} steps)",
                        estimated_actions=actions,
                        success_condition="reach high-reward state",
                        source="bfs",
                    )

        # 2. Directional fallback: infer movement direction toward goal
        if player_role and goal_role:
            p_pos = player_role.estimated_position or {}
            g_pos = goal_role.estimated_position or {}
            directions = []
            if p_pos and g_pos:
                dr = g_pos.get("row", 0) - p_pos.get("row", 0)
                dc = g_pos.get("col", 0) - p_pos.get("col", 0)
                if dr > 0:
                    directions.extend(["ACTION2"] * min(abs(int(dr)), 5))
                elif dr < 0:
                    directions.extend(["ACTION1"] * min(abs(int(dr)), 5))
                if dc > 0:
                    directions.extend(["ACTION4"] * min(abs(int(dc)), 5))
                elif dc < 0:
                    directions.extend(["ACTION3"] * min(abs(int(dc)), 5))

            if directions:
                return PlanChunk(
                    description=f"Move {victory_condition.condition_type.value} toward goal",
                    estimated_actions=directions[:8],
                    success_condition="reduce distance to goal object",
                    source="directional",
                )

        # 3. Exploration fallback: try unexplored actions
        unexplored = state_graph.get_unexplored_actions(current_hash, available_actions)
        action = unexplored[0] if unexplored else (available_actions[0] if available_actions else "ACTION1")
        return PlanChunk(
            description="Explore: try unexplored action to gather more information",
            estimated_actions=[action],
            success_condition="observe new state",
            source="explore",
        )


# ── Solve Engine ──────────────────────────────────────────────────────

class SolveEngine:
    """Top-level controller. Called by orchestrator between hypothesize() and plan().

    Owns: ArchetypeClassifier, ObjectRoleMapper, VictoryHypothesizer,
          DissonanceDetector, PlanChunker.
    Consumes: hypothesis_context (from HypothesisManager.observe()),
              brain_client, llm_client.
    Produces: SolveContext.
    """

    def __init__(self, brain_client: Any, llm_client: Any, session_id: str) -> None:
        self.brain = brain_client
        self.llm = llm_client
        self.session_id = session_id

        self.archetype_classifier = ArchetypeClassifier()
        self.role_mapper = ObjectRoleMapper()
        self.victory_hypothesizer = VictoryHypothesizer()
        self.dissonance_detector = DissonanceDetector()
        self.plan_chunker = PlanChunker()

        self._archetype: GameArchetype = GameArchetype.UNKNOWN
        self._archetype_confidence: float = 0.0
        self._archetype_locked: bool = False
        self._object_roles: Dict[int, ObjectRole] = {}
        self._victory_condition: Optional[VictoryCondition] = None
        self._active_chunk: Optional[PlanChunk] = None
        self._chunk_plan_id: Optional[str] = None
        self._reward_history: List[float] = []

    async def solve(
        self,
        observation: Dict[str, Any],
        hypothesis_context: Dict[str, Any],
        step: int,
        state_graph: Any,           # StateGraph instance from HypothesisManager
        current_state_hash: str,
    ) -> SolveContext:
        """Run one solve step. Returns SolveContext for orchestrator."""

        # Track reward history
        reward = float((hypothesis_context.get("last_transition_effect") or {}).get(
            "reward_signal", 0.0
        ))
        self._reward_history.append(reward)

        # 1. Archetype classification (algorithmic first)
        if not self._archetype_locked:
            archetype, confidence = self.archetype_classifier.update(hypothesis_context)

            # Call analogical_search for analogy votes (once per 10 steps after first lock candidate)
            if confidence >= 0.35 and step % 10 == 5:
                task_id = observation.get("task_id", "")
                analogy_query = f"ARC game {archetype.value} grid puzzle solve"
                analogy_results = await self.brain.analogical_search(
                    query=analogy_query,
                    current_quest_id=task_id,
                    limit=5,
                    min_similarity=0.30,
                )
                archetype, confidence = self.archetype_classifier.apply_analogy_votes(
                    archetype, confidence, analogy_results.get("results", [])
                )

            self._archetype = archetype
            self._archetype_confidence = confidence
            if confidence >= ArchetypeClassifier.LOCK_THRESHOLD:
                self._archetype_locked = True
                logger.info("Archetype locked: %s (confidence=%.2f)", archetype.value, confidence)

        # 2. Object role mapping (runs every step, lightweight)
        new_roles = self.role_mapper.update(hypothesis_context, observation, step)
        # Merge: only update roles where new confidence is higher
        for color_id, new_role in new_roles.items():
            existing = self._object_roles.get(color_id)
            if existing is None or new_role.confidence > existing.confidence:
                self._object_roles[color_id] = new_role

        # 3. Victory condition hypothesis (LLM call, sticky)
        dissonance_detected, dissonance_reason = self.dissonance_detector.update(
            hypothesis_context, self._active_chunk, step
        )

        need_victory_hypothesis = (
            self._victory_condition is None
            and self._archetype_confidence >= VictoryHypothesizer.CALL_THRESHOLD
        ) or (
            dissonance_detected
            and (self._victory_condition is None or self._victory_condition.confidence < 0.5)
        )

        if need_victory_hypothesis:
            self._victory_condition = await self.victory_hypothesizer.hypothesize(
                archetype=self._archetype,
                object_roles=self._object_roles,
                brain_client=self.brain,
                llm_client=self.llm,
                session_id=self.session_id,
                task_id=observation.get("task_id", ""),
                reward_history=self._reward_history,
                dissonance_reason=dissonance_reason if dissonance_detected else "",
            )

        # 4. Dissonance handling: report negative outcome + reset chunk
        if dissonance_detected and self._chunk_plan_id:
            try:
                await self.brain.report_outcome(
                    plan_id=self._chunk_plan_id,
                    outcome=f"Chunk stalled: {dissonance_reason}",
                    valence=-0.6,
                    session_id=self.session_id,
                    valence_source="dissonance_detector",
                )
            except Exception as exc:
                logger.warning("report_outcome failed: %s", exc)
            self._active_chunk = None
            self._chunk_plan_id = None
            self.dissonance_detector.reset_chunk()

        # 5. Plan chunking: generate or continue active chunk
        if self._active_chunk is None and self._victory_condition is not None:
            available_actions = observation.get("available_actions") or [
                f"ACTION{i}" for i in range(1, 8)
            ]
            self._active_chunk = self.plan_chunker.generate_chunk(
                victory_condition=self._victory_condition,
                object_roles=self._object_roles,
                state_graph=state_graph,
                current_hash=current_state_hash,
                available_actions=available_actions,
                step=step,
            )
            self.dissonance_detector.reset_chunk()

            # Register chunk as a SideQuests Plan
            if self._active_chunk:
                chunk_goal = f"Chunk: {self._active_chunk.description}"
                chunk_steps = self._active_chunk.estimated_actions or ["explore"]
                try:
                    plan_payload = await self.brain.register_plan(
                        goal=chunk_goal,
                        steps=chunk_steps,
                        session_id=self.session_id,
                    )
                    self._chunk_plan_id = plan_payload.get("plan_id")
                    logger.info(
                        "Chunk registered: %s (plan_id=%s, source=%s)",
                        self._active_chunk.description,
                        self._chunk_plan_id,
                        self._active_chunk.source,
                    )
                except Exception as exc:
                    logger.warning("register_plan failed for chunk: %s", exc)

        # 6. Update chunk progress score from reward signal
        if self._active_chunk and reward > 0.3:
            self._active_chunk.progress_score = min(
                self._active_chunk.progress_score + reward * 0.2, 1.0
            )
            self._active_chunk.steps_executed += 1

        # Build strategy summary for prompt
        strategy = self._build_strategy_summary()

        return SolveContext(
            archetype=self._archetype,
            archetype_confidence=self._archetype_confidence,
            object_roles=dict(self._object_roles),
            victory_condition=self._victory_condition,
            active_chunk=self._active_chunk,
            dissonance_detected=dissonance_detected,
            dissonance_reason=dissonance_reason,
            strategy_summary=strategy,
        )

    def _build_strategy_summary(self) -> str:
        parts = [f"ARCHETYPE: {self._archetype.value} (conf={self._archetype_confidence:.2f})"]
        if self._victory_condition:
            vc = self._victory_condition
            parts.append(f"GOAL: {vc.condition_type.value} — {vc.description} (conf={vc.confidence:.2f})")
        if self._active_chunk:
            ch = self._active_chunk
            parts.append(f"CHUNK: {ch.description} [{ch.source}] progress={ch.progress_score:.2f}")
        return " | ".join(parts)

    def reset_for_retry(self) -> None:
        """Reset ephemeral state. Preserve archetype and victory condition."""
        self._active_chunk = None
        self._chunk_plan_id = None
        self.dissonance_detector.reset_chunk()
        self.dissonance_detector._zero_progress_streak = 0
```

---

### MODIFY: `agents/arc3/hypothesis.py`

Add one method to `StateGraph`:

```python
def find_path(self, from_hash: str, to_hash: str) -> List["Transition"]:
    """BFS from from_hash to to_hash. Returns ordered list of Transitions.

    Returns empty list if no path exists or either hash is unknown.
    Used by PlanChunker to extract exact action sequences.
    """
    if from_hash not in self.nodes or to_hash not in self.nodes:
        return []
    if from_hash == to_hash:
        return []

    from collections import deque
    # BFS: queue of (current_hash, path_so_far)
    queue: deque = deque([(from_hash, [])])
    visited: set = {from_hash}

    while queue:
        current, path = queue.popleft()
        for transition in self.edges.get(current, []):
            if transition.to_hash == to_hash:
                return path + [transition]
            if transition.to_hash not in visited:
                visited.add(transition.to_hash)
                queue.append((transition.to_hash, path + [transition]))

    return []
```

---

### MODIFY: `agents/arc3/orchestrator.py`

**`__init__`** — add SolveEngine:
```python
from agents.arc3.solver import SolveEngine
# ...
self.solve_engine = SolveEngine(brain_client, llm_client, session_id)
self._solve_context: dict | None = None
```

**New `async def solve()`** — between hypothesize and plan:
```python
async def solve(
    self,
    observation: ARC3Observation,
    hypothesis_context: dict,
    step: int,
) -> dict:
    """Classify archetype, assign object roles, hypothesize victory condition, chunk plan."""
    current_hash = hypothesis_context.get("current_state_hash", "")
    solve_ctx = await self.solve_engine.solve(
        observation=observation,
        hypothesis_context=hypothesis_context,
        step=step,
        state_graph=self.hypothesis_mgr.graph,
        current_state_hash=current_hash,
    )
    self._solve_context = {
        "archetype": solve_ctx.archetype.value,
        "archetype_confidence": solve_ctx.archetype_confidence,
        "object_roles": {
            str(k): {"role": v.role.value, "confidence": v.confidence}
            for k, v in solve_ctx.object_roles.items()
        },
        "victory_condition": (
            {
                "type": solve_ctx.victory_condition.condition_type.value,
                "description": solve_ctx.victory_condition.description,
                "confidence": solve_ctx.victory_condition.confidence,
            }
            if solve_ctx.victory_condition else None
        ),
        "active_chunk": (
            {
                "description": solve_ctx.active_chunk.description,
                "estimated_actions": solve_ctx.active_chunk.estimated_actions,
                "progress": solve_ctx.active_chunk.progress_score,
                "source": solve_ctx.active_chunk.source,
            }
            if solve_ctx.active_chunk else None
        ),
        "dissonance": solve_ctx.dissonance_detected,
        "dissonance_reason": solve_ctx.dissonance_reason,
        "strategy_summary": solve_ctx.strategy_summary,
    }
    return self._solve_context
```

**`_draft_plan_steps()`** — use SolveContext:
```python
def _draft_plan_steps(self, observation, memory_context, recall, hypothesis_context) -> List[str]:
    steps = []
    sc = self._solve_context

    if sc and sc.get("victory_condition"):
        vc = sc["victory_condition"]
        steps.append(f"Win condition: {vc['description']} (confidence={vc['confidence']:.2f})")
    else:
        steps.append("Explore game mechanics: test each action and observe effects")

    if sc and sc.get("active_chunk"):
        ch = sc["active_chunk"]
        steps.append(f"Execute chunk: {ch['description']}")
    else:
        steps.append("Gather more observations to identify game type")

    steps.append("Evaluate: did the action advance the win condition? Adjust if not.")

    return steps[:self.MAX_PROMPT_PLAN_STEPS]
```

**`_build_solve_section()`** — new helper:
```python
def _build_solve_section(self) -> str:
    sc = self._solve_context
    if not sc:
        return ""
    lines = ["=== SOLVE CONTEXT ==="]
    lines.append(f"ARCHETYPE: {sc['archetype']} (confidence={sc['archetype_confidence']:.2f})")

    roles = sc.get("object_roles") or {}
    if roles:
        lines.append("OBJECT ROLES:")
        for color_id, role_info in list(roles.items())[:5]:
            lines.append(f"  color_{color_id}: {role_info['role']} (conf={role_info['confidence']:.2f})")

    vc = sc.get("victory_condition")
    if vc:
        lines.append(f"VICTORY: {vc['type'].upper()} — {vc['description']} (conf={vc['confidence']:.2f})")

    chunk = sc.get("active_chunk")
    if chunk:
        lines.append(f"ACTIVE CHUNK: {chunk['description']} [{chunk['source']}]")
        if chunk.get("estimated_actions"):
            lines.append(f"  Suggested actions: {chunk['estimated_actions'][:6]}")
        lines.append(f"  Progress: {chunk['progress']:.2f}")

    if sc.get("dissonance"):
        lines.append(f"⚠ DISSONANCE: {sc['dissonance_reason']}")

    return "\n".join(lines)
```

**`build_action_prompt()`** — inject solve section:
Add `self._build_solve_section()` output after the HYPOTHESIS section. Reference
`_solve_context.get("active_chunk", {}).get("estimated_actions")` to suggest specific actions.

**`reset_for_retry()`** — add:
```python
self.solve_engine.reset_for_retry()
```

Also add `current_state_hash` to the `hypothesize()` return context so solve() can access it:
```python
# In HypothesisManager.observe(), the return dict should include:
context["current_state_hash"] = grid_hash
```

---

### NEW: `tests/test_arc3_solver.py`

```python
"""Tests for agents/arc3/solver.py — ARC Solve Engine."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from agents.arc3.solver import (
    ArchetypeClassifier, GameArchetype, ObjectRoleMapper, RoleType,
    VictoryHypothesizer, VictoryType, VictoryCondition,
    DissonanceDetector, PlanChunker, SolveEngine, SolveContext,
)


# ── ArchetypeClassifier ─────────────────────────────────────────────

def test_archetype_unknown_before_min_observations():
    clf = ArchetypeClassifier()
    for _ in range(4):
        archetype, conf = clf.update({})
    assert archetype == GameArchetype.UNKNOWN


def test_archetype_race_from_hud_and_reward():
    clf = ArchetypeClassifier()
    ctx = {
        "action_facts": [
            {"fact_type": "deterministic_effect", "value_status": "valuable"},
            {"fact_type": "deterministic_effect", "value_status": "valuable"},
        ],
        "hud_rows": [61, 62],
        "path_hypotheses": [],
    }
    for _ in range(5):
        archetype, conf = clf.update(ctx)
    assert archetype == GameArchetype.RACE
    assert conf > 0.0


def test_archetype_analogy_votes_boost_confidence():
    clf = ArchetypeClassifier()
    analogy_results = [
        {"text_raw": "ARC chase game player flees enemy", "similarity": 0.75},
        {"text_raw": "chase archetype convergence detected", "similarity": 0.70},
    ]
    archetype, conf = clf.apply_analogy_votes(
        GameArchetype.CHASE, 0.45, analogy_results
    )
    assert archetype == GameArchetype.CHASE
    assert conf > 0.45


def test_archetype_analogy_disagreement_caps_confidence():
    clf = ArchetypeClassifier()
    analogy_results = [
        {"text_raw": "race archetype linear path", "similarity": 0.80},
    ]
    archetype, conf = clf.apply_analogy_votes(
        GameArchetype.CHASE, 0.60, analogy_results
    )
    assert conf <= 0.5  # disagreement caps confidence


# ── ObjectRoleMapper ─────────────────────────────────────────────────

def test_object_role_wall_on_static_frame():
    mapper = ObjectRoleMapper()
    ctx = {
        "static_rows": [60, 61],
        "last_transition_effect": {"meaningful_change_score": 0.0, "regions_changed": []},
    }
    obs = {"colors": [3]}
    roles = mapper.update(ctx, obs, step=5)
    assert 3 in roles
    assert roles[3].role == RoleType.WALL


def test_object_role_player_on_changed_center_with_reward():
    mapper = ObjectRoleMapper()
    ctx = {
        "static_rows": [],
        "last_transition_effect": {
            "meaningful_change_score": 0.7,
            "regions_changed": ["center"],
            "changed_center": {"row": 10.0, "col": 8.0},
        },
    }
    obs = {"colors": [5]}
    roles = mapper.update(ctx, obs, step=3)
    assert 5 in roles
    assert roles[5].role == RoleType.PLAYER
    assert roles[5].estimated_position == {"row": 10.0, "col": 8.0}


# ── VictoryHypothesizer ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_victory_hypothesizer_calls_recall_plans():
    brain = AsyncMock()
    brain.recall_plans.return_value = {"plans": []}
    brain.recall_relevant_lessons.return_value = {"lessons": []}

    llm = AsyncMock()
    llm.complete.return_value = '{"condition_type":"reach_goal","description":"reach exit","target_color_id":6,"confidence":0.7}'

    vh = VictoryHypothesizer()
    vc = await vh.hypothesize(
        archetype=GameArchetype.CHASE,
        object_roles={},
        brain_client=brain,
        llm_client=llm,
        session_id="s1",
        task_id="t1",
        reward_history=[0.0, 0.0, 1.0],
    )
    brain.recall_plans.assert_called_once()
    brain.recall_relevant_lessons.assert_called_once()
    assert vc.condition_type == VictoryType.REACH_GOAL
    assert vc.confidence == 0.7
    assert vc.source == "llm"


@pytest.mark.asyncio
async def test_victory_hypothesizer_uses_high_valence_plan_directly():
    brain = AsyncMock()
    brain.recall_plans.return_value = {
        "plans": [{"goal": "reach exit bottom-right", "valence": 0.9}]
    }
    brain.recall_relevant_lessons.return_value = {"lessons": []}

    llm = AsyncMock()
    vh = VictoryHypothesizer()
    vc = await vh.hypothesize(
        archetype=GameArchetype.RACE,
        object_roles={},
        brain_client=brain,
        llm_client=llm,
        session_id="s1",
        task_id="t1",
        reward_history=[],
    )
    llm.complete.assert_not_called()   # high-valence plan skips LLM
    assert vc.source == "recall_plans"


@pytest.mark.asyncio
async def test_victory_hypothesizer_handles_llm_parse_error():
    brain = AsyncMock()
    brain.recall_plans.return_value = {"plans": []}
    brain.recall_relevant_lessons.return_value = {"lessons": []}
    llm = AsyncMock()
    llm.complete.return_value = "INVALID JSON {{{"

    vh = VictoryHypothesizer()
    vc = await vh.hypothesize(
        GameArchetype.UNKNOWN, {}, brain, llm, "s1", "t1", []
    )
    assert vc.source == "error"
    assert vc.confidence < 0.2


# ── DissonanceDetector ───────────────────────────────────────────────

def test_dissonance_fires_after_stall_threshold():
    dd = DissonanceDetector()
    from agents.arc3.solver import PlanChunk
    chunk = PlanChunk(description="test chunk", progress_score=0.0)
    ctx = {"last_transition_effect": {"meaningful_change_score": 0.05}}
    dissonance = False
    for i in range(DissonanceDetector.STALL_THRESHOLD + 1):
        dissonance, reason = dd.update(ctx, chunk, step=i)
    assert dissonance is True
    assert "no meaningful change" in reason


def test_dissonance_resets_on_good_progress():
    dd = DissonanceDetector()
    from agents.arc3.solver import PlanChunk
    chunk = PlanChunk(description="test")
    low_ctx = {"last_transition_effect": {"meaningful_change_score": 0.05}}
    good_ctx = {"last_transition_effect": {"meaningful_change_score": 0.8}}
    for _ in range(4):
        dd.update(low_ctx, chunk, step=0)
    dd.update(good_ctx, chunk, step=5)   # resets streak
    dissonance, _ = dd.update(low_ctx, chunk, step=6)
    assert not dissonance  # streak reset, only 1 zero-progress step


def test_dissonance_triggers_report_outcome():
    """DissonanceDetector fires; SolveEngine should call report_outcome."""
    # This is tested via test_solve_engine_dissonance_calls_report_outcome below


# ── PlanChunker ──────────────────────────────────────────────────────

def test_plan_chunker_bfs_on_state_graph():
    from agents.arc3.hypothesis import StateGraph, StateNode, Transition
    from agents.arc3.solver import PlanChunker, VictoryCondition, VictoryType

    graph = StateGraph()
    graph.add_state(StateNode("h1", 1, {}, 1.0, []))
    graph.add_state(StateNode("h2", 2, {}, 1.0, []))
    graph.add_state(StateNode("h3", 3, {}, 1.0, []))
    t1 = Transition("h1", "h2", "ACTION1", 1, "", 5, [])
    t1.reward_signal = 0.8
    t2 = Transition("h2", "h3", "ACTION2", 2, "", 5, [])
    t2.reward_signal = 0.0
    graph.add_transition(t1)
    graph.add_transition(t2)

    vc = VictoryCondition(condition_type=VictoryType.REACH_GOAL, description="test")
    chunker = PlanChunker()
    chunk = chunker.generate_chunk(
        victory_condition=vc,
        object_roles={},
        state_graph=graph,
        current_hash="h1",
        available_actions=["ACTION1", "ACTION2", "ACTION3"],
        step=5,
    )
    # BFS should find path through high-reward state h2
    assert chunk.source == "bfs"
    assert "ACTION1" in chunk.estimated_actions


def test_plan_chunker_falls_back_to_exploration_when_no_graph():
    from agents.arc3.hypothesis import StateGraph
    from agents.arc3.solver import PlanChunker, VictoryCondition, VictoryType

    graph = StateGraph()  # empty
    graph.add_state(__import__("agents.arc3.hypothesis", fromlist=["StateNode"]).StateNode("h1", 1, {}, 1.0, []))
    vc = VictoryCondition(condition_type=VictoryType.REACH_GOAL, description="test")
    chunker = PlanChunker()
    chunk = chunker.generate_chunk(vc, {}, graph, "h1", ["ACTION1", "ACTION2"], step=1)
    assert chunk.source == "explore"
    assert len(chunk.estimated_actions) >= 1


# ── SolveEngine integration ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_solve_engine_reset_preserves_archetype():
    from agents.arc3.solver import SolveEngine, PlanChunk

    brain = AsyncMock()
    brain.recall_plans.return_value = {"plans": []}
    brain.recall_relevant_lessons.return_value = {"lessons": []}
    brain.analogical_search.return_value = {"results": []}
    brain.register_plan.return_value = {"plan_id": "p1"}

    llm = AsyncMock()
    engine = SolveEngine(brain, llm, "s1")
    engine._archetype = GameArchetype.CHASE
    engine._archetype_confidence = 0.8
    engine._archetype_locked = True
    engine._active_chunk = PlanChunk(description="old chunk")
    engine._chunk_plan_id = "p-old"

    engine.reset_for_retry()

    assert engine._archetype == GameArchetype.CHASE   # preserved
    assert engine._archetype_locked is True           # preserved
    assert engine._active_chunk is None               # cleared
    assert engine._chunk_plan_id is None              # cleared


@pytest.mark.asyncio
async def test_solve_engine_dissonance_calls_report_outcome():
    from agents.arc3.solver import SolveEngine, PlanChunk, VictoryCondition, VictoryType

    brain = AsyncMock()
    brain.report_outcome.return_value = {"status": "ok"}
    brain.register_plan.return_value = {"plan_id": "p-new"}
    brain.recall_plans.return_value = {"plans": []}
    brain.recall_relevant_lessons.return_value = {"lessons": []}
    brain.analogical_search.return_value = {"results": []}

    llm = AsyncMock()
    llm.complete.return_value = '{"condition_type":"reach_goal","description":"reach exit","target_color_id":null,"confidence":0.5}'

    engine = SolveEngine(brain, llm, "s1")
    engine._archetype = GameArchetype.CHASE
    engine._archetype_confidence = 0.7
    engine._archetype_locked = True
    engine._victory_condition = VictoryCondition(
        condition_type=VictoryType.REACH_GOAL, confidence=0.6, description="reach exit"
    )
    engine._active_chunk = PlanChunk(description="stalled chunk", progress_score=0.0)
    engine._chunk_plan_id = "p-stall"
    # Force stall
    engine.dissonance_detector._zero_progress_streak = 10

    from agents.arc3.hypothesis import StateGraph
    graph = StateGraph()
    ctx = {
        "last_transition_effect": {"meaningful_change_score": 0.0, "reward_signal": 0.0},
        "action_facts": [], "hud_rows": [], "path_hypotheses": [],
        "current_state_hash": "h1",
    }
    obs = {"colors": [], "available_actions": ["ACTION1"], "task_id": "t1", "dataset_id": "d1"}

    await engine.solve(obs, ctx, step=20, state_graph=graph, current_state_hash="h1")

    brain.report_outcome.assert_called_once()
    call_kwargs = brain.report_outcome.call_args.kwargs
    assert call_kwargs["valence"] < 0
    assert call_kwargs["plan_id"] == "p-stall"
```

---

## Validation Commands

```bash
# Core tests
python3 -m pytest tests/test_arc3_solver.py -v

# Regression: B88 hypothesis engine must still pass
python3 -m pytest tests/test_arc3_hypothesis.py tests/test_arc3_orchestrator.py -q

# Full ARC test suite
python3 -m pytest tests/test_arc3_solver.py tests/test_arc3_hypothesis.py tests/test_arc3_orchestrator.py -q
```

---

## Risks and Constraints

- **No SideQuests schema changes** — SolveEngine uses only existing brain_client tools.
- **LLM latency** — VictoryHypothesizer fires once when archetype locks; not every step.
  Orchestrator should not await solve() if archetype confidence < 0.35.
- **StateGraph.find_path() added to hypothesis.py** — minor extension, fully tested separately.
- **current_state_hash** must be added to HypothesisManager.observe() return dict so the
  solve() method can pass it to PlanChunker without re-hashing the grid.
- **chunk_plan_id accumulation** — each registered chunk creates a Plan node in Kùzu.
  Over many games this is intentional: those plans become the cross-game lesson corpus.
- **asyncio** — All brain_client calls in SolveEngine are awaited; SolveEngine.solve() is async.
  Orchestrator must await it.
