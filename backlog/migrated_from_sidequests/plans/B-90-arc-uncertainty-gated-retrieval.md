# B-90-arc-uncertainty-gated-retrieval — ARC Uncertainty-Gated Retrieval

**Card:** B90 | **Priority:** P1 | **Depends on:** B89

## Summary

Make ARC retrieval trigger-based instead of always-on so SideQuests behaves like targeted decision support rather than a context loader. The prompt should also ask for the next action from observed effects, not generic pattern talk.

## Technical Approach

### Retrieval Triggers
- initial puzzle bootstrapping
- repeated no-progress steps
- fallback or invalid-action correction
- loop suspicion
- large state-shift trigger if cheaply detectable
- evidence gap where the current observation and short history are not enough to choose a valid next action

### Prompting Rule
- baseline prompt stays small
- retrieval payload enters the prompt only when a trigger fires
- triggered retrieval should be compact and reason-specific
- action request should foreground what changed, what effect the last move had, and what decision to make next
- avoid free-form pattern narration unless it directly changes the next move

## Concrete File Changes

- add retrieval-trigger logic in `agents/arc3/orchestrator.py`
- update ARC prompt strategy docs with trigger definitions
- update the prompt template so the first question is decision-oriented and effect-based
- add tests covering trigger and no-trigger paths

## API/Schema/Test Updates

- no new external APIs
- no schema changes expected
- add pytest coverage for retrieval gating behavior

## Acceptance Criteria

1. Retrieval can be skipped when no trigger fires
2. Retrieval happens when at least one defined trigger fires
3. Prompt size is smaller on the no-trigger path than on the triggered path
4. The action prompt is framed around observed effects and next decision choice
5. Tests validate no-trigger and triggered behavior

## Validation Commands

- `.venv/bin/pytest -q tests/test_arc3_orchestrator.py`
- `.venv/bin/pytest -q tests/test_arc3_durable_runner.py`

## Notes on Risks or Constraints

- Do not accidentally remove the minimum stable operating context
- Avoid adding too many triggers; the point is disciplined expansion, not another form of prompt bloat
