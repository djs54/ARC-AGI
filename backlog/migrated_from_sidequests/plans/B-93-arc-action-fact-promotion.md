# B-93-arc-action-fact-promotion — ARC Action Fact Promotion During Explore Phase

## Goal

Promote repeated explore-phase action evidence into compact SideQuests action facts, distinct from path hypotheses.

## Implementation Plan

1. Define promotion thresholds for:
   - deterministic visible effect
   - no-op / blocked action
   - loop-causing action
2. Add compact fact text generation in the ARC hypothesis layer.
3. Save promoted facts separately from:
   - valuable/confirmed action decisions
   - path hypotheses
4. Make later prompt retrieval prefer promoted action facts over generic repeated turn text.

## Acceptance Criteria

- Repeated operator evidence can create a durable action fact
- Consistent-but-low-value actions are not marked as successful strategies
- Action facts are stored and described separately from path hypotheses
- Tests cover positive, no-op, and low-value cases
