# Plan: A209 — Audit: Does `check_budget` Need to Route Through the Reasoner?

## Card metadata

- ID: A209
- Priority: P2
- Layer: ARC runtime
- Dependencies: A202, A207

## Summary

Settle, with real reasoning, whether `WorkflowOrchestrator.run()`'s `check_budget` termination path (fires before the try block, before the Reasoner can ever be consulted) is a legitimate exemption from Shift B's single-reasoning-owner principle, or the same class of gap `second_veto` was (fixed in A207). This is an investigation-first card — do not write the "if not exempt" code until step 1-2 below produce an actual answer.

## Technical approach

### 1. Re-read the primary sources, not this card's summary

- `ARCHITECTURE.md`'s "Graph-Engineering Principles (Shift A/B/C)" section — the actual adopted text for Shift A and Shift B. Note precisely what makes something exempt from single-agent reasoning ownership per that text (not per this plan's paraphrase).
- `docs/superpowers/specs/2026-08-23-trajectory-reasoner-design.md` section 5 — this already reasoned through the `termination_from_evaluation` exemption once (the self-review correction referenced in A202's card). Read the actual argument made there, not a summary of it.
- `agents/arc4/workflow.py`'s current `run()` — confirm `check_budget`'s exact position relative to the try block and the Reasoner call site (may have shifted since this plan was written; re-locate before reasoning about it).
- `backlog/A207.md`'s Outcome section — the `second_veto` investigation's actual reasoning for why THAT path needed the Reasoner, to compare against.

### 2. Answer the actual question

Write out, in this card's Outcome section (not scratch notes elsewhere): what specifically distinguishes `check_budget` from `second_veto`, if anything? Candidate distinctions to actually test against the source material, not just assert:

- **"It's deterministic"** — insufficient alone; `second_veto`'s vet decision is also deterministic (the vetter is not an LLM call), and that didn't exempt it. If this is the reasoning, explain what ELSE makes budget different from a deterministic veto.
- **"It's about resource exhaustion, not investigation strategy"** — plausible distinction: `check_budget` doesn't represent a failed hypothesis or a blocked plan, it represents "we're out of time/steps regardless of what's happening." Test this against the counter-argument in A209's own card: the Reasoner might have real information (how close was it to a resolution) that gets discarded unread when budget silently ends the episode. Does "resource exhaustion" actually make that information irrelevant, or does it just make the *deadline* non-negotiable while the *reason recorded for stopping* could still benefit from the Reasoner's view?
- **Precedent from `termination_from_evaluation`** — that exemption's actual justification (per the design spec) is that it's an *authoritative external fact* (the game itself declared win/loss). `check_budget` is not an external fact — it's an internal configuration value (`max_cycles`). Does the same justification actually transfer, or was A207's card's use of "the same Shift-A reason" an overextension of a different principle to a case it wasn't built for?

### 3a. If the audit concludes "exempt, no change needed"

Write the reasoning into `backlog/A209.md`'s Outcome section in full, mark the card complete, no code changes. This is a legitimate outcome — do not manufacture a fix to have something to ship.

### 3b. If the audit concludes "not exempt, needs a fix"

`check_budget` fires before `perceive` runs for that cycle — there is no `perception`/`execution`/`evaluation` for the current cycle at all, unlike `second_veto` (which at least has `perception_payload` from earlier in the same cycle). Two candidate shapes, evaluate both against what's actually useful rather than defaulting to the first one:

- **Option A: synthetic empty cycle**, mirroring A207's `second_veto` pattern as closely as possible — construct a synthetic `PerceptionSnapshot`/`ExecutionResult`/`EvaluationResult` representing "nothing happened this cycle," call the Reasoner with `stall_reason="budget_exhausted"` (reusing the stall-fold mechanism a third time). Simple, consistent with precedent, but the Reasoner would be reasoning about a cycle that never actually ran — potentially misleading signal.
- **Option B: reuse the previous cycle's real state.** `WorkflowOrchestrator` already has `perception_payload`/`resolved_goal_payload`/the last real `execution`/`evaluation` from the PRECEDING iteration still in scope (they're loop-local variables from the last successful pass through the try block). Give the Reasoner one last look at the actual last real cycle's results, with a distinct signal (e.g. a new kwarg, not reusing `stall_reason`) meaning "this is the final cycle, budget is exhausted, is there anything you want recorded before this ends" — closer to genuine information than a synthetic empty cycle, but requires `run()` to retain those variables across the loop-top `check_budget` check (they may not be in scope on the very FIRST iteration, when no prior cycle exists yet — handle that case explicitly, e.g. skip the Reasoner call entirely if there's no prior cycle to report on).

Pick whichever option the investigation in steps 1-2 actually supports as meaningful, not whichever is easier to implement. If neither option produces a decision that changes anything real (e.g. the Reasoner's answer can never override the budget ceiling itself — running out of budget still ends the episode either way), consider whether a fix is even worth building versus just recording the reasoning and leaving `check_budget` as an exempt, deterministic hard stop that the Reasoner is told about via telemetry only (not given decision authority over).

## Concrete file changes

| File | Change |
|------|--------|
| `backlog/A209.md` | Outcome section documents the audit's finding either way |
| `agents/arc4/workflow.py` | Only if step 3b's investigation concludes a fix is warranted |
| `tests/test_a209_*.py` (new) | Only if a fix lands |

## Tests

Only applicable if 3b's fix lands. Sketch (adapt based on which option is chosen):

1. `check_budget` firing invokes the Reasoner (if configured) before `BUDGET_EXHAUSTED` is returned.
2. No Reasoner configured — behavior is byte-for-byte unchanged (regression guard, matching every prior card's convention).
3. First-ever cycle hitting budget zero (`max_cycles=0`) with no prior real cycle to report on — does not crash, handles the "nothing to report" case explicitly (relevant only for Option B).
4. The Reasoner's response cannot override the budget ceiling — the episode still ends as `BUDGET_EXHAUSTED` regardless of what the Reasoner returns (this is the one thing that must NOT change: budget is a hard ceiling, not a negotiation).

## Validation commands

```bash
# If a fix lands:
.venv/bin/python -m pytest tests/test_a209_*.py -v
.venv/bin/python -m pytest tests/test_a202_reasoner_orchestrator_integration.py tests/test_arc4_workflow.py -v
make test-a
make test-all
```

## Assumptions/defaults

- This plan deliberately does not pre-decide the outcome. If the implementer's own reading of the primary sources produces a different, well-reasoned conclusion than either option sketched above, that's an acceptable outcome — document the actual reasoning, don't force-fit one of these two shapes if a better one is evident once the real code and specs are in front of you.
- Whatever is decided, `check_budget` must remain a genuinely hard ceiling — no version of this fix should make it possible for a Reasoner decision to extend the episode past `max_cycles`.
