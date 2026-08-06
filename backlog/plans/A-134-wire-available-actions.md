# Plan: A-134 — Wire Available Actions From Game Server Into Observation

## Context

The planner has a full scoring engine (graph evidence, goal alignment, exponential decay, forced exploration, vet gate) but only ever sees 1 candidate because the observation lacks `available_actions`. The MCP game server knows the action space — this plan wires it through.

## Investigation needed

Before implementing, determine where the action space lives:

1. **ARC game server response** — What does the MCP game server return after each action? Does it include available moves, clickable cells, or tool options? Check `campy/adapters/mcp_server.py` in the hippocampy repo for the response schema.

2. **Perception pipeline** — How does `run_single_puzzle.py` construct the observation dict that's passed to `perceive`? Trace from game server response → observation dict → perceive phase.

3. **ARC-AGI-3 action semantics** — What are the actual actions in ARC-AGI-3? Grid cell clicks? Tool selections? Pattern submissions? The action IDs need to be meaningful to the planner.

## Approach (once investigation complete)

### Option A: Game server already returns actions

If the MCP response includes available actions (e.g., clickable grid cells, tool buttons):

1. In `run_single_puzzle.py` or the perception adapter, extract the action list from the game server response
2. Include it as `observation["available_actions"] = [...]`
3. The planner's `_available_actions()` already checks this field — no planner changes needed

### Option B: Actions must be inferred from grid state

If the game server only returns grid state:

1. Add an action-space inference step in the perceive phase
2. From the grid shape and game type, enumerate possible actions (e.g., click each cell, use each tool)
3. Populate `perception.metadata["available_actions"]`

### Option C: Actions come from the graph world model

If the graph's `arc_get_untested_actions` tool provides the action space:

1. Wire `graph_port.get_untested_actions()` into the planner or perceive phase
2. Combine with any static action enumeration

## Files likely to modify

- `run_single_puzzle.py` — observation construction
- `agents/arc4/perceive.py` — action space inference
- Possibly `sidequest_mcp_client/` — MCP response parsing (respecting MCP seam boundary)

## Risks

- ARC-AGI-3 may have a very large action space (e.g., every grid cell × every color). Need to cap candidates to avoid combinatorial explosion.
- Action IDs must be stable across steps so `action_attempt_counts` tracking works.
