# ARC-AGI-3 Prompt Strategy

This document is the operating contract for prompt construction in the ARC runner and orchestrator.

## Goal

SideQuests should reduce decision-time prompt load, not amplify it.

For ARC, the prompt should contain only what changes the next action.

## Current Design Principle

The target is:

`prompt = stable operating frame + compact state summary + top-ranked decision support + short recent history + explicit valid actions + action request`

In plain language:
- small by default
- retrieval on demand
- compact summaries over raw dumps
- only include memory that can change the next move

## Prompt Equation

The current ARC action prompt should be composed of:

1. Stable operating frame
- role
- puzzle state
- energy
- valid action list

2. Compact state summary
- grid dimensions
- top color distribution
- frame hash
- coarse spatial summary

3. Top-ranked decision support
- at most 1 lesson
- at most 1 memory
- at most 1 analogy
- exclude boilerplate ARC API contract reminders from the action prompt

4. Hypothesis layer
- loop warning if present
- at most 1 confirmed hypothesis
- at most 1 active hypothesis
- untested actions list

5. Plan layer
- at most 2 plan steps

6. Recent history
- at most 2 prior actions
- concise rationale snippets only

7. Action request
- explicit output schema
- explicit valid actions
- ask for the next decision from observed effects, not generic pattern commentary

## First-Input Rule

The first puzzle-ingestion packet can be slightly richer than the per-step action prompt.

It should include compact structural features that help retrieval and pre-activation pattern match quickly:
- frame hash
- compact spatial sketch
- stable ids
- concise observed puzzle structure

This is not permission to stuff the action prompt. It is a targeted bootstrap for retrieval quality.

## Meaningful Change Equation

Yes: ARC should also have an explicit effect-value equation, not just a prompt equation.

The point is to stop treating "many pixels changed" as automatically good. For ARC control, action value should depend mainly on progress, novelty, and reward, with pixel delta only as a weak supporting signal.

Current rule:

`meaningful_change = 0.40 * reward + 0.25 * progress + 0.25 * novelty + 0.10 * effect_visibility - 0.35 * loop_penalty - 0.25 * no_change_penalty - repeat_zero_reward_penalty`

Where:
- `reward` = immediate reward signal from the environment
- `progress` = whether the action moved the puzzle into a genuinely new, non-terminal state
- `novelty` = whether the resulting state was new rather than a revisit
- `effect_visibility` = a small tie-breaker based on visible board change
- `loop_penalty` = penalty when the action leads back into a visited loop
- `no_change_penalty` = penalty when the action produces no visible change
- `repeat_zero_reward_penalty` = decay repeated exploitation when the same action keeps generating non-rewarding states

Additional decay rule:
- after 2 consecutive zero-reward uses of the same action, novelty and progress should decay unless the new attempt has stronger evidence than the previous ones

Interpretation bands:
- `>= 0.75` → `strong_progress`
- `>= 0.35` → `tentative_progress`
- `> 0.00` → `low_value`
- `0.00` → `no_progress`

Prompting rule:
- begin in an explicit exploration phase
- before exploiting, get at least one observed effect for each available action when the budget allows
- prefer `strong_progress`
- use `tentative_progress` for bounded exploration
- avoid repeating `low_value` or `no_progress` actions unless new evidence appears
- do not keep exploiting `tentative_progress` forever when reward stays at `0.0`
- if the top tested actions both decay into `low_value` or `no_progress`, broaden exploration rather than bouncing between them
## Section Ownership Rules (B110)

To reduce repetition and ensure clear context, each section has strict ownership:

- `ACTION FACTS`: operator facts only (how an action behaves)
- `PATH HYPOTHESES`: path/sequence facts only (complex action chains)
- `OBSERVED EFFECTS`: latest transition evidence only (what just happened)
- `SOLVE CONTEXT` and `PLAN`: goal/chunk level only (strategy and objectives)
- `INSTRUCTION`: decision policy only; refer to above sections instead of re-dumping them

## Prompt Composition Pass (B110)

Before final assembly, a deduplication and compression pass is performed:
- repeated facts across sections are merged or suppressed
- prefer references (e.g., "refer to ACTION FACTS") over re-stating evidence
- `OBSERVATION` is suppressed or significantly reduced when `OBSERVED EFFECTS` already contains enough board context to choose the next action

## Exploration Compaction (B116)

To preserve long-run knowledge without context bloat, the engine generates an `EXPLORATION_SUMMARY` section:
- `KNOWN ACTION EFFECTS`: compact, deterministic operator facts.
- `KNOWN LOOPS`: sequences that were confirmed to return the state to a previous hash.
- `CONFIRMED RULES`: validated high-confidence mechanical hypotheses.

This section allows the agent to recall what it "already learned" even after raw `HISTORY` has been truncated.

## Mental Sandbox (B114)

Before committing to an action, the agent enters a bounded internal reasoning loop (max 2 iterations).
- Use `sandbox_thought` to peek at how an action aligns with known `ACTION FACTS` and the active `PLAN CHUNK`.
- The sandbox does not spend environment steps or energy.
- Final decisions are annotated with `(sandbox refined)` if self-correction occurred.

## Ledger-Driven Pruning (B118)

To maintain high performance and low latency, the harness monitors the SideQuests call ledger:
- Call types are analyzed by grouping ledger entries and computing average latency and low-value ratio.
- Pruning is triggered when: **average latency > 500ms** AND **low-value ratio > 50%** (i.e., > 50% of calls returned zero results).
- Pruned call types (e.g., `current_truth`, `recall_lessons`, `analogical_search`) are down-ranked and may be skipped during mid-run retrieval triggers.
- Low-value detection is pattern-based: result summaries containing "found 0" or "found []" (case-insensitive).
- Pruning decisions are logged with their reasoning and preserved in the debug export (`pruning_decisions` field) for transparency and post-hoc analysis.

## Typed Decision Packets (B117)

To ensure maintainable prompt assembly, the engine uses structured packets:
- `PromptPacket`: a typed collection of `ContentBlocks`.
- `ContentBlock`: a single logical section (e.g., `MEMORY`, `PLAN`, `OBSERVATION`) with optional custom headers.
- The packet model allows for programmatic transformations (like observation suppression or deduplication) before final rendering.

### Block Types and Ordering

The standard block ordering and ownership:

1. **SYSTEM** - System message (operational context, available actions)
2. **STATE** - Current puzzle state and energy estimate
3. **MEMORY** - Retrieved lessons, memories, analogies (optional, retrieval-triggered)
4. **SOLVE_CONTEXT** - Archetype, object roles, victory condition, active chunk
5. **PLAN** - High-level plan steps and approach
6. **ACTION_FACTS** - Deterministic operator facts (how actions behave)
7. **EXPLORATION_SUMMARY** - Compacted exploration state (B116, optional)
8. **PATH_HYPOTHESES** - Path and sequence hypotheses, exploration coverage
9. **HYPOTHESIS** - Confirmed and active hypotheses
10. **OBSERVED_EFFECTS** - Latest transition evidence and action effects
11. **REFLEX** - Warnings and golden-path suggestions (optional)
12. **HISTORY** - Recent action history with rationales
13. **OBSERVATION** - Current grid observation (suppressed by B110 if redundant)
14. **INSTRUCTION** - Decision policy and action request

### Programmatic Transformations

Before rendering, the packet layer applies transformations:
- **B110**: Suppresses OBSERVATION when OBSERVED_EFFECTS provides sufficient board context
- **B114**: Mental sandbox logic can annotate INSTRUCTION with sandbox refinements
- **B116**: Exploration compaction populates EXPLORATION_SUMMARY
- Future: Multimodal content, guard annotations, decision chains

### Packet Construction Flow

1. `build_action_packet()` creates ContentBlock instances for each decision surface
2. `_apply_packet_transformations()` applies B110/B114/B116 logic on structured blocks
3. `packet.render()` produces final prompt string with ordered headers
4. `build_action_prompt()` is the public interface that builds and renders the packet

## Hard Limits

These are the current implementation limits for the first prompt-slimming pass:

- `lessons <= 1`
- `memories <= 1`
- `analogies <= 1`
- `history steps <= 2`
- `plan steps <= 2`
- `confirmed hypotheses <= 1`
- `active hypotheses <= 1`
- `reflex warnings <= 1`
- `reflex suggestions <= 1`
- retrieval payload size should stay compact enough that the action prompt remains decision-first

## Anti-Goals

Do not:
- dump the full raw `64x64` grid every step
- inject repeated ARC API contract text into action prompts
- replay full memory payloads or graph node JSON blobs
- include memory just because it was retrieved
- allow unavailable actions returned by the LLM to pass through unvalidated

## Retrieval Rule

Memory exists to compress the next decision, not narrate the system state.

If retrieved context does not alter the next action choice, it should stay out of the prompt.

On the first move, memory should not steer action selection unless it clearly matches the current puzzle state.

Retrieval triggers should be narrow and explicit:
- puzzle bootstrap
- repeated no-progress steps
- invalid-action or fallback correction
- loop suspicion
- large state shift that can invalidate prior assumptions
- evidence gaps where the current observation and short history are not enough to choose the next action

## B89 Comparison Method

Use puzzle 1 as the fixed comparison target and compare result rows with one compact first-input shape
against one richer first-input shape.

Recommended report fields:
- `tokens_input`
- `runtime_seconds`
- `steps`
- `invalid_action_count`
- `no_progress_step_count`
- `first_prompt_detail_level`
- `asked_for_decision_from_effects`
- `retrieval_count`
- `total_retrieval_size_bytes`

The comparison should answer two questions:
- Did the richer first input improve retrieval usefulness without blowing the prompt budget?
- Did the compressed prompt keep the agent grounded in observed effects instead of generic pattern talk?

## Backlog Direction

Longer term, ARC should move toward:
- minimal default prompt state
- content-window-free retrieval on demand
- passive pre-activation of likely-needed entities and paths
- fast delivery of compact summaries instead of bulk context

North star:

**small, purposeful context with fast, targeted retrieval**
