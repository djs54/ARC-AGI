"""Prompts for ARC3 agent components."""

SYSTEM_PROMPT = (
    "SYSTEM: You are a skilled ARC-AGI-3 agent. Your goal is to solve complex grid transformation "
    "puzzles by discovering rules through interactive levels. "
    "Available actions: {available_actions}"
)

INSTRUCTION_TEMPLATE = (
    "INSTRUCTION:\n"
    "{effect_summary}\n\n"
    "Choose the next valid action based on observed effects. "
    "Return JSON: {{\"action_id\": \"...\", \"rationale\": \"...\"}}"
)

SANDBOX_INSTRUCTION = (
    "\n\nMENTAL SANDBOX: You can simulate actions before committing to them. "
    "Use `sandbox_thought` to predict the grid state after your proposed action."
)

REPL_SANDBOX_INSTRUCTION = (
    "\n\nREPL SANDBOX: You can write Python code to analyze the grid or verify transformations."
)

SANDBOX_SYSTEM_MESSAGE = (
    "You are an ARC mental sandbox. Simulate the requested action and return the result."
)

QUERY_LLM_SYSTEM_MESSAGE = (
    "You are an ARC decision engine. Analyze the context and return the best action as JSON."
)

VICTORY_HYPOTHESIS_TEMPLATE = (
    "Analyze the game archetype and victory condition for this ARC puzzle.\n\n"
    "ARCHETYPE: {archetype}\n"
    "OBJECT ROLES:\n{object_roles}\n\n"
    "PAST PLANS:\n{past_plans}\n\n"
    "LESSONS:\n{lessons}\n\n"
    "HISTORY:\n{reward_summary}\n\n"
    "Based on these signals, what is the win condition?\n"
    "Return JSON: {{\"condition_type\": \"...\", \"description\": \"...\", \"target_color_id\": N, \"confidence\": 0.X}}"
)

TRANSFORMATION_HYPOTHESIS_TEMPLATE = (
    "Analyze the transformation rule based on these examples:\n"
    "Evidence: {evidence}\n"
    "Examples: {n_examples}\n"
    "What is the Python transform function?"
)

# Phase 1: Pattern discovery (UNDERSTAND phase)
ARC_PATTERN_SYSTEM_PROMPT = (
    "You are solving an ARC puzzle. Discover the rule that transforms "
    "input grids to output grids, then apply it to the test input."
)

ARC_PATTERN_INSTRUCTION_TEMPLATE = (
    "TRAINING EXAMPLES:\n{training_examples}\n\n"
    "GRID ANALYSIS: {grid_analysis}\n\n"
    "{hypothesis_section}"
    "{repl_section}"
    "What is the transformation rule? Write a Python function "
    "def transform(grid) that implements it.\n"
    "Return JSON: {{\"rule\": \"<description>\", \"python\": \"<function code>\"}}"
)

# Phase 2 execution mode: solution is known, just paint it
ARC_EXECUTION_SYSTEM_PROMPT = (
    "You are submitting your answer to an ARC puzzle. You know the correct "
    "output grid. Use the available actions to paint it onto the test grid."
)

ARC_EXECUTION_INSTRUCTION_TEMPLATE = (
    "TARGET GRID (paint this):\n{target_grid}\n\n"
    "CURRENT GRID:\n{current_grid}\n\n"
    "CELLS TO CHANGE:\n{cells_to_paint}\n\n"
    "Available actions: {available_actions}\n"
    "Return JSON: {{\"action_id\": \"...\", \"coordinates\": [row, col], \"color\": N}}"
)

# Phase 2 fallback: existing navigation prompt
ARC_ACTION_INSTRUCTION_TEMPLATE = (
    "Based on your analysis, choose the best action.\n"
    "Available: {available_actions}\n"
    "Return JSON: {{\"action_id\": \"...\", \"rationale\": \"...\"}}"
)

# solver.py GameRuleHypothesizer — hypothesis prompt (B151)
GAME_RULE_HYPOTHESIS_TEMPLATE = """You are playing an ARC-AGI-3 interactive game with {total_levels} levels.
You have solved {n_solved} level(s). Based on the evidence below, hypothesize the GAME RULES.

Solved level summaries:
{level_summaries}

Action effects observed:
{action_effects}

Cross-level patterns:
{cross_level_pattern}

Respond with EXACTLY this JSON format (no other text):
{{
  "rule_description": "<one sentence: what is this game about?>",
  "action_semantics": {{"ACTION1": "<what it does>", "ACTION2": "<what it does>", "ACTION3": "<what it does>", "ACTION4": "<what it does>", "ACTION5": "<what it does>", "ACTION6": "<what it does>"}},
  "objective_description": "<what does winning a level require?>",
  "level_strategy": "<approach for the next level>",
  "confidence": <0.0-1.0>
}}"""

# repl_verification.py HypothesisRefinementLoop — refinement prompt (B152)
REPL_REFINEMENT_TEMPLATE = """Your ARC transformation function failed on {n} of {total} training examples.

Current function:
{current_function}

Example that failed:
Input grid:
{input_grid}

Expected output:
{expected_grid}

Your output:
{actual_grid}

Difference: {diff_summary}

Fix the function. Return ONLY the corrected Python function (def transform(grid): ...).
No explanation, just the code."""

# B126: Verification sub-agent — adversarial check after action proposal
VERIFIER_SYSTEM_PROMPT = "You are a critical verifier for ARC action decisions. Your job is to find flaws, not to agree."

VERIFIER_PROMPT_TEMPLATE = """ACTION VERIFICATION

Proposed action: {action_id}
Rationale: {rationale}

Current observation:
- State: {state}
- Colors: {colors}
- Shapes: {shapes}
- Recent history: {recent_history}
- Sandbox result: {sandbox_result}
- Loop detected: {loop_detected}
- Known action facts: {action_facts_summary}

Should this action be approved or rejected? Focus on likely failure modes.
Return JSON: {{"approved": true, "reason": "..."}} or {{"approved": false, "reason": "..."}}"""

# rule_application mode (B153)
ARC_LEVEL_INSIGHT_TEMPLATE = """You are playing level {current_level} of {total_levels} in an ARC game.
From prior levels, you've learned:
- Action effects: {action_semantics}
- Game rule: {rule_hypothesis}
- Confidence: {confidence:.0%}

Apply this knowledge to solve the current level.
"""

# exploration mode (B153)
ARC_EXPLORATION_TEMPLATE = """You are playing level 1 of an ARC game.
You need to discover what each action does by experimenting.
Try each action and observe the effect.
"""

# compact mode for small local models (B164)
COMPACT_SYSTEM_PROMPT = (
    "You are solving an ARC grid puzzle. Available actions: {available_actions}. "
    "Think step by step: what changed, what seems blocked, and what should you try next?"
)

COMPACT_INSTRUCTION_TEMPLATE = (
    "Think step by step. What changed after your last action, and what should you try next?\n"
    "Return JSON: {\"action\": N, \"why\": \"...\"}"
)

# A077: World Model Guided Planner Section
PLANNER_CANDIDATES_SECTION = (
    "\n\nPLANNER PROPOSALS (based on world model evidence):\n"
    "{planner_candidates}\n"
    "Consider these evidence-backed actions when choosing your next step."
)
