# B-94-arc-path-hypotheses — ARC Path Hypothesis Composition and Testing

## Goal

Add a lightweight path-hypothesis layer above action facts so ARC can reason about short sequences and their results.

## Implementation Plan

1. Build short path summaries from recent transitions.
2. Classify each path as valuable, tentative, low-value, or ineffective.
3. Surface path hypotheses in prompt/debug output separately from action facts.
4. Add tests for path generation and prompt rendering.

## Acceptance Criteria

- Recent 2-step or 3-step sequences can be summarized as path hypotheses
- Prompt output shows path hypotheses distinctly from action facts
- Tests cover generation and rendering
