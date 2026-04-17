# B-92-arc-write-trace — ARC Step-Level SideQuests Write Trace

## Goal

Make ARC debug output show what SideQuests actually saved on each step, not just what the environment returned.

## Implementation Plan

1. Add a write-trace collector in the ARC orchestrator for SideQuests-facing writes created during each step.
2. Record compact write summaries:
   - type
   - status
   - text summary
   - source step
3. Attach those summaries to the exported ARC result JSON as a per-step trace.
4. Keep the export stable and compact enough for real puzzle debugging.

## Acceptance Criteria

- Each ARC step can be inspected as:
  - prompt/action/effect
  - SideQuests write summaries
- The trace is available in `submission_results_single.json`
- Tests validate the new exported structure
