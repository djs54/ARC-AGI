"""ARC-AGI-3 API knowledge seed for SideQuests ingestion.

Ingests the verified interface contract into SideQuests memory so that
perceive() and recall_relevant_lessons() can surface this knowledge when
the agent is reasoning about actions, states, and strategy.
"""

from __future__ import annotations

# Structured knowledge chunks derived from benchmarks/arc3/interface_contract.md.
# Each chunk is a self-contained fact that can be ingested as a separate turn
# so the consolidation loop can index it independently.

API_KNOWLEDGE_CHUNKS: list[str] = [
    # ── Action semantics ─────────────────────────────────────────────
    (
        "ARC-AGI-3 has exactly 7 actions. "
        "ACTION1=up, ACTION2=down, ACTION3=left, ACTION4=right, "
        "ACTION5=interact/select/rotate/attach/execute, "
        "ACTION6=coordinate action requiring (x,y) in 0-63 range, "
        "ACTION7=undo. Actions are game-relative abstractions — the agent "
        "must discover what each action does through exploration."
    ),
    # ── Available actions ────────────────────────────────────────────
    (
        "The available_actions field in every FrameResponse tells the agent "
        "which actions are valid RIGHT NOW. This is dynamic per frame. "
        "The agent MUST only choose from available_actions, not from all 7. "
        "ACTION6 availability does not reveal which (x,y) coordinates are active."
    ),
    # ── FrameResponse schema ─────────────────────────────────────────
    (
        "Every action returns a FrameResponse with: "
        "frame (64x64 grid, pixel values 0-15), "
        "state (NOT_FINISHED|NOT_STARTED|WIN|GAME_OVER), "
        "available_actions (array of valid action IDs), "
        "levels_completed (int), win_levels (int), "
        "action_input (echo of triggering action)."
    ),
    # ── State transitions ────────────────────────────────────────────
    (
        "State transitions: NOT_STARTED -> NOT_FINISHED (after RESET+first action). "
        "NOT_FINISHED -> WIN (level solved) or GAME_OVER (failed). "
        "WIN means the agent solved the current level. "
        "GAME_OVER means the agent exhausted attempts or made a terminal mistake. "
        "There is NO partial-credit reward per step — only state transitions."
    ),
    # ── Causality discovery ──────────────────────────────────────────
    (
        "The API does NOT tell the agent what actions do. "
        "The agent must infer causality by comparing the grid BEFORE and AFTER "
        "each action. No diff is provided — the agent must compute its own delta. "
        "No error messages are returned for bad in-game moves; the grid may "
        "simply be unchanged."
    ),
    # ── Session lifecycle ────────────────────────────────────────────
    (
        "Episode lifecycle: RESET with game_id+card_id -> guid. "
        "Loop actions using same guid. Observe state after each command. "
        "Terminal states are WIN or GAME_OVER. "
        "Two consecutive RESET calls guarantee a fully fresh game."
    ),
    # ── Grid properties ──────────────────────────────────────────────
    (
        "Grid is 64x64 pixels. Each pixel value is an integer 0-15 (16 colors). "
        "ACTION6 coordinates (x,y) are bounded to [0, 63]. "
        "The frame field in the response is an array of frames; each frame "
        "is 64x64 integers."
    ),
    # ── Strategy heuristics ──────────────────────────────────────────
    (
        "Strategy: When available_actions narrows, the environment is "
        "constraining the solution space — pay attention. "
        "If an action produces no grid change, it was likely invalid or "
        "a no-op in the current state. Try a different action. "
        "If levels_completed increments, the current strategy is working. "
        "If state becomes GAME_OVER, the strategy failed — report negative "
        "valence and try a different approach next time."
    ),
    # ── Energy / life bar (B88 generic HUD discovery) ─────────────────
    (
        "The game HUD (heads-up display) layout varies by game. "
        "Energy bars, score indicators, and inventory may appear in different "
        "grid regions. The agent must DISCOVER HUD elements by observing which "
        "rows/regions remain static across multiple actions, then hypothesize "
        "their meaning. Do not assume fixed HUD row positions."
    ),
]

# B108: Precomputed knowledge cache to bypass Step 2/3b LLM calls.
# This makes ingestion of stable protocol concepts near-instant.
API_KNOWLEDGE_CACHE: dict[str, dict] = {
    API_KNOWLEDGE_CHUNKS[0]: {
        "entities": [
            {"text": "ARC-AGI-3", "gist_class": "Category", "schema_org_type": "DefinedTerm", "label": "ORG"},
            {"text": "ACTION1", "gist_class": "PlannedEvent", "schema_org_type": "Action", "label": "ORG"},
            {"text": "ACTION2", "gist_class": "PlannedEvent", "schema_org_type": "Action", "label": "ORG"},
            {"text": "ACTION3", "gist_class": "PlannedEvent", "schema_org_type": "Action", "label": "ORG"},
            {"text": "ACTION4", "gist_class": "PlannedEvent", "schema_org_type": "Action", "label": "ORG"},
            {"text": "ACTION5", "gist_class": "PlannedEvent", "schema_org_type": "Action", "label": "ORG"},
            {"text": "ACTION6", "gist_class": "PlannedEvent", "schema_org_type": "Action", "label": "ORG"},
            {"text": "ACTION7", "gist_class": "PlannedEvent", "schema_org_type": "Action", "label": "ORG"},
            {"text": "0-63", "gist_class": "Magnitude", "schema_org_type": "QuantitativeValue", "label": "CARDINAL"},
        ],
        "relations": []
    },
    API_KNOWLEDGE_CHUNKS[1]: {
        "entities": [
            {"text": "available_actions", "gist_class": "Category", "schema_org_type": "DefinedTerm", "label": "NOUN_CHUNK"},
            {"text": "FrameResponse", "gist_class": "PhysicalThing", "schema_org_type": "Product", "label": "ORG"},
            {"text": "ACTION6", "gist_class": "PlannedEvent", "schema_org_type": "Action", "label": "ORG"},
        ],
        "relations": []
    },
    API_KNOWLEDGE_CHUNKS[2]: {
        "entities": [
            {"text": "FrameResponse", "gist_class": "PhysicalThing", "schema_org_type": "Product", "label": "ORG"},
            {"text": "64x64", "gist_class": "Magnitude", "schema_org_type": "QuantitativeValue", "label": "CARDINAL"},
            {"text": "0-15", "gist_class": "Magnitude", "schema_org_type": "QuantitativeValue", "label": "CARDINAL"},
            {"text": "available_actions", "gist_class": "Category", "schema_org_type": "DefinedTerm", "label": "NOUN_CHUNK"},
            {"text": "levels_completed", "gist_class": "Magnitude", "schema_org_type": "QuantitativeValue", "label": "NOUN_CHUNK"},
            {"text": "state", "gist_class": "Category", "schema_org_type": "DefinedTerm", "label": "NOUN_CHUNK"},
        ],
        "relations": []
    },
    API_KNOWLEDGE_CHUNKS[3]: {
        "entities": [
            {"text": "State transitions", "gist_class": "Event", "schema_org_type": "Event", "label": "NOUN_CHUNK"},
            {"text": "RESET", "gist_class": "PlannedEvent", "schema_org_type": "Action", "label": "ORG"},
            {"text": "WIN", "gist_class": "Event", "schema_org_type": "Event", "label": "NOUN_CHUNK"},
            {"text": "GAME_OVER", "gist_class": "Event", "schema_org_type": "Event", "label": "NOUN_CHUNK"},
        ],
        "relations": [
            {"head": "RESET", "relation_type": "ENABLES", "tail": "State transitions", "confidence": 0.9}
        ]
    },
    API_KNOWLEDGE_CHUNKS[4]: {
        "entities": [
            {"text": "API", "gist_class": "PhysicalThing", "schema_org_type": "Product", "label": "ORG"},
            {"text": "causality", "gist_class": "Category", "schema_org_type": "DefinedTerm", "label": "NOUN_CHUNK"},
            {"text": "grid", "gist_class": "PhysicalThing", "schema_org_type": "Product", "label": "NOUN_CHUNK"},
            {"text": "delta", "gist_class": "Magnitude", "schema_org_type": "QuantitativeValue", "label": "NOUN_CHUNK"},
        ],
        "relations": []
    },
    API_KNOWLEDGE_CHUNKS[5]: {
        "entities": [
            {"text": "Episode lifecycle", "gist_class": "Event", "schema_org_type": "Event", "label": "NOUN_CHUNK"},
            {"text": "RESET", "gist_class": "PlannedEvent", "schema_org_type": "Action", "label": "ORG"},
            {"text": "guid", "gist_class": "Category", "schema_org_type": "DefinedTerm", "label": "NOUN_CHUNK"},
            {"text": "WIN", "gist_class": "Event", "schema_org_type": "Event", "label": "NOUN_CHUNK"},
            {"text": "GAME_OVER", "gist_class": "Event", "schema_org_type": "Event", "label": "NOUN_CHUNK"},
        ],
        "relations": []
    },
    API_KNOWLEDGE_CHUNKS[6]: {
        "entities": [
            {"text": "Grid", "gist_class": "PhysicalThing", "schema_org_type": "Product", "label": "NOUN_CHUNK"},
            {"text": "64x64", "gist_class": "Magnitude", "schema_org_type": "QuantitativeValue", "label": "CARDINAL"},
            {"text": "0-15", "gist_class": "Magnitude", "schema_org_type": "QuantitativeValue", "label": "CARDINAL"},
            {"text": "ACTION6", "gist_class": "PlannedEvent", "schema_org_type": "Action", "label": "ORG"},
        ],
        "relations": []
    },
    API_KNOWLEDGE_CHUNKS[7]: {
        "entities": [
            {"text": "Strategy", "gist_class": "PlannedEvent", "schema_org_type": "Action", "label": "NOUN_CHUNK"},
            {"text": "available_actions", "gist_class": "Category", "schema_org_type": "DefinedTerm", "label": "NOUN_CHUNK"},
            {"text": "GAME_OVER", "gist_class": "Event", "schema_org_type": "Event", "label": "NOUN_CHUNK"},
        ],
        "relations": []
    },
    API_KNOWLEDGE_CHUNKS[8]: {
        "entities": [
            {"text": "HUD", "gist_class": "PhysicalThing", "schema_org_type": "Product", "label": "ORG"},
            {"text": "Energy bars", "gist_class": "PhysicalThing", "schema_org_type": "Product", "label": "NOUN_CHUNK"},
            {"text": "score indicators", "gist_class": "PhysicalThing", "schema_org_type": "Product", "label": "NOUN_CHUNK"},
            {"text": "inventory", "gist_class": "PhysicalThing", "schema_org_type": "Product", "label": "NOUN_CHUNK"},
        ],
        "relations": []
    }
}


async def ingest_api_knowledge(
    brain_client,
    session_id: str,
) -> int:
    """Ingest all API knowledge chunks into SideQuests memory.

    B108: Uses API_KNOWLEDGE_CACHE to bypass expensive consolidation steps.
    Returns the number of chunks ingested.
    """
    for chunk in API_KNOWLEDGE_CHUNKS:
        precomputed = API_KNOWLEDGE_CACHE.get(chunk)
        await brain_client.notify_turn(
            role="system",
            content=f"[ARC-AGI-3 API Contract] {chunk}",
            session_id=session_id,
            precomputed=precomputed,
        )
    return len(API_KNOWLEDGE_CHUNKS)
