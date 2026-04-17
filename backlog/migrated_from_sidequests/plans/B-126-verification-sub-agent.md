# B-126 Plan - Verification Sub-Agent

Card: B126
Priority: P2
Dependencies: B114, B115

## Summary
Add adversarial verifier prompt stage after candidate action selection to catch novel bad moves before execution.

## Ecosystem / Ownership
- Layer: Agent Runtime & Harness.
- Prompt templates live in shared prompt module for ARC runtime.

## Technical Approach
1. Add verifier prompts in `agents/arc3/prompts.py`:
- `VERIFIER_SYSTEM_PROMPT`
- `VERIFIER_PROMPT_TEMPLATE`
2. Add `_verify_candidate_action()` in `agents/arc3/orchestrator.py`.
3. Flow in `act()`:
- candidate from solver/guard
- verifier returns APPROVE or rejection reason
- if rejection: one retry with rejection context
- if second rejection: fall through to original candidate (bounded)
4. Record verifier decisions in `thinking_trace` and guard/debug outputs.

## Concrete File Changes
- Modify: `agents/arc3/orchestrator.py`
- Modify: `agents/arc3/prompts.py`
- Modify tests: `tests/test_arc3_orchestrator.py` and/or `tests/test_b126_verifier.py`

## Test Plan
- `pytest -q tests/test_arc3_orchestrator.py`
- `pytest -q tests/test_b115_decision_guard.py tests/test_b114_mental_sandbox.py`

## Acceptance Criteria Mapping
- Verifier called once per decision path.
- Rejection triggers exactly one retry.
- Double rejection falls through without loop.
- Trace/debug output includes verifier status and rationale.

## Risks / Constraints
- Keep extra model calls bounded.
- Ensure no prompt/phase ownership violations.
