# A092-A095 Exploration Complete — Executive Summary

**Date**: May 6, 2026  
**Baseline Status**: Green (make test-a passes)  
**Scope**: Codebase structure assessment for A092-A095 terminal-alignment sequence

---

## What Was Delivered

Three comprehensive reference documents have been created to guide A092-A095 implementation:

1. **[A092-A095_CODEBASE_SURVEY.md](A092-A095_CODEBASE_SURVEY.md)** (1800 lines)
   - Full architectural survey of current infrastructure
   - Section-by-section breakdown of existing patterns
   - Integration points clearly marked for each card
   - Critical blockers and decisions identified

2. **[A092-A095_IMPLEMENTATION_CHECKLIST.md](A092-A095_IMPLEMENTATION_CHECKLIST.md)** (400 lines)
   - Method-by-method breakdown of what needs to be added/modified
   - Test template patterns
   - Config changes needed
   - Acceptance criteria checklist

3. **[A092-A095_CODE_PATTERNS.md](A092-A095_CODE_PATTERNS.md)** (600 lines)
   - Actual code snippets showing current patterns
   - How to extend each pattern for A092-A095
   - Telemetry integration examples
   - Common mistakes to avoid

---

## Critical Findings

### ✅ Already in Place (A073-A090 Foundation)

- **Graph schema**: Per-game world model with nodes (Action, Effect, Observation, Hypothesis, Mechanic) and edges (CAUSED, CONTRADICTS, SUPPORTS, etc.)
- **Effect recording**: `world_model.record_effect()` creates causal chain Action→Effect→Observation
- **Prediction generation**: `world_model_planner._generate_prediction_for_action()` returns structured dicts
- **Falsification conditions**: `_generate_falsification_condition_for_action()` produces string rules
- **Ranking framework**: `_rank_candidates_by_evidence_backing()` sorts by evidence tier
- **Decision framework**: `ReasoningMode` enum and `ReasoningDecision` dataclass
- **Prompt packet system**: `PromptPacket.render()` with ordered blocks
- **Snapshot emission**: `_emit_world_model_decision_snapshot()` writes JSONL telemetry

### ❌ Missing or Incomplete (Blockers for A092-A095)

| Card | Missing Component | Impact |
|------|-------------------|--------|
| **A092** | Goal distance computation | Cannot classify terminal-aligned vs object_local |
| **A092** | Effect alignment metadata | Cannot use alignment in planner ranking |
| **A093** | Prediction miss recording | Cannot track falsification evidence |
| **A093** | Action quarantine state | Cannot suppress failed actions |
| **A094** | Exhaustion check method | Cannot detect when multi-action churn is futile |
| **A094** | Exhaustion decision emission | Multi_action_churn_exhausted never sent downstream |
| **A095** | Delta computation | Cannot compress repeated prompt context |
| **A095** | Compressed packet rendering | Cannot reduce tokens per step |

---

## Integration Architecture

### Data Flow (Step-by-Step)

```
Step N: Action execution
  ↓
Step N+1: Observation received
  ↓
1. World model records: record_effect(action, effect, obs)
   └→ Compiler computes: effect.alignment_class
   
2. Planner ranks candidates:
   - Uses: alignment_class, quarantine_state, prior_compatibility
   - Suppresses: quarantined actions, regressing-terminal object_progress
   - Prefers: terminal_aligned effects (tier -1)
   
3. Reasoning controller decides:
   - Checks: exhaustion criteria (all actions evidenced + no terminal progress)
   - Checks: delayed-effect guard (don't give up too early)
   - Emits: ReasoningMode.MULTI_ACTION_STRATEGY_EXHAUSTED
   
4. Orchestrator handles:
   - Calls: world_model.record_prediction_miss() if prediction wrong
   - Calls: world_model.quarantine_action() after 2 high-conf misses
   - Builds: compressed prompt (A095) using get_delta_since()
   
5. Telemetry captures:
   - alignment_tier, terminal_distance, quarantine_count
   - world_model_decision, prompt_compression_ratio
```

### Key Seams (Where Code Connects)

| Seam | Source | Target | What Passes |
|------|--------|--------|------------|
| **Compiler** | orchestrator → compiler | world_model | goal_model (for computing alignment) |
| **Falsification** | orchestrator → world_model | — | prediction_miss events (with confidence) |
| **Quarantine** | world_model → planner | — | quarantine_state (read in ranking) |
| **Decision** | reasoning_controller → runner | — | ReasoningDecision with world_model_decision field |
| **Delta** | world_model → orchestrator | — | get_delta_since() for compression |
| **Telemetry** | all → runner | eval | metrics dict with new fields |

---

## Implementation Sequence (Recommended)

### Phase 1: Foundation (A092)
1. Add `compute_goal_distance()` to grid_analysis.py
2. Add `classify_effect_alignment()` to world_model_compiler.py
3. Modify `compile()` to accept goal_model and compute alignments
4. Store alignment_class on Effect nodes
5. Tests: flat, regressing, improving terminal scenarios

**Why first?** Terminal alignment is prerequisite for everything else. Cleanest dependencies.

### Phase 2: Falsification (A093)
1. Add `record_prediction_miss()` to world_model.py
2. Add quarantine methods: `quarantine_action()`, `is_action_quarantined()`, `get_quarantine_state()`
3. Modify orchestrator to detect mismatches and call record_prediction_miss()
4. Modify planner ranking to check quarantine and suppress tier
5. Tests: 2 misses quarantine, TTL expiry, fallback

**Why second?** Depends on A092 (alignment affects what counts as "meaningful" prediction). Can proceed in parallel once A092 started.

### Phase 3: Exhaustion Decision (A094)
1. Add `check_multi_action_exhaustion()` to world_model.py
2. Add exhaustion check to reasoning_controller.decide()
3. Emit ReasoningMode.MULTI_ACTION_STRATEGY_EXHAUSTED with world_model_decision
4. Modify orchestrator to handle decision (early stop or reclassify)
5. Modify runner to capture decision in JSONL
6. Tests: all evidenced + no progress → exhausted; delayed guard defers

**Why third?** Depends on A093 (quarantine state affects evidence count). Must have decision framework working.

### Phase 4: Compression (A095)
1. Add `get_delta_since()` to world_model.py
2. Add delta template to prompts.py
3. Modify `_build_prompt()` to use delta on step > 1
4. Define REQUIRED_BLOCKS constant
5. Track compression metrics
6. Tests: 60%+ token drop; required content retained

**Why last?** Lowest priority, depends on settled world model. Can be done last without blocking other work.

---

## Quick Reference: File Touch Map

```
Must modify:
  ✓ agents/arc3/world_model.py — graph queries + state
  ✓ agents/arc3/world_model_compiler.py — alignment classification
  ✓ agents/arc3/world_model_planner.py — ranking logic
  ✓ agents/arc3/reasoning_controller.py — exhaustion decision
  ✓ agents/arc3/orchestrator.py — orchestration + compression
  ✓ agents/arc3/prompts.py — delta template
  ✓ agents/arc3/grid_analysis.py — goal distance
  ✓ benchmarks/arc3/world_model_eval.py — telemetry

May need to modify:
  ? agents/arc3/runner.py — telemetry capture (if not via snapshot)
  ? config.yaml — new reasoning_gate parameters

Must create:
  ✓ tests/test_a092_*.py
  ✓ tests/test_a093_*.py
  ✓ tests/test_a094_*.py
  ✓ tests/test_a095_*.py
```

---

## Dependency Matrix

```
A073 (graph)
  ↓
A074 (compiler)
  ↓
A089 (graph predictions) + A090 (prior ranking)
  ↓
┌─ A092 (terminal alignment)
│   ↓
│   └─ A093 (falsification)
│       ↓
│       └─ A094 (exhaustion decision)
│           ↓
│           └─ A095 (compression)
└─ Parallel with A085 (churn gate)
```

**Critical**: A092 → A093 → A094 → A095 form a sequence. Earlier cards must be green before starting later cards.

---

## Testing Strategy

Each card has 5-6 test cases covering:
- **Happy path**: Feature works as designed
- **Edge cases**: Boundary conditions (low confidence, no evidence, TTL expiry)
- **Integration**: Feature works with other components
- **Fallback**: System degrades gracefully when feature doesn't apply
- **Telemetry**: Metrics captured correctly

Test files provided as templates in code_patterns.md.

---

## Acceptance Gate

Run smoke test to verify baseline still passes:
```bash
make test-a
make smoke
```

Then for each card, run card-specific test suite:
```bash
pytest tests/test_a092_*.py -v
pytest tests/test_a093_*.py -v
pytest tests/test_a094_*.py -v
pytest tests/test_a095_*.py -v
```

---

## Known Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Goal model not available to compiler | Pass as optional parameter; fail gracefully if None |
| Quarantine state becomes stale | Implement TTL expiry check in is_action_quarantined() |
| Exhaustion triggers too early | Include delayed-effect guard; require 0.7+ confidence prior |
| Compression loses required info | Define REQUIRED_BLOCKS list; test coverage verification |
| Telemetry fields not in eval schema | Create schema first; add fields before implementation |

---

## Resources

- **Session memory**: `/memories/session/arc_baseline_exploration.md` (detailed technical notes)
- **Survey**: `A092-A095_CODEBASE_SURVEY.md` (architecture overview)
- **Checklist**: `A092-A095_IMPLEMENTATION_CHECKLIST.md` (method-by-method tasks)
- **Patterns**: `A092-A095_CODE_PATTERNS.md` (code examples)
- **Backlog**: `backlog/A092.md`, `backlog/A093.md`, `backlog/A094.md`, `backlog/A095.md` (requirements)
- **Plans**: `backlog/plans/A-092-*.md` etc. (detailed plans if created)

---

## Next Steps

1. **Review this summary** with team
2. **Start with A092** (terminal alignment) — cleanest scope
3. **Create test file first** (TDD pattern)
4. **Implement methods** in order: world_model → compiler → planner → controller → orchestrator
5. **Run tests incrementally** to catch integration issues early
6. **Move to A093** once A092 tests pass

**Estimated effort**: 
- A092: ~2-3 days (algorithm + tests)
- A093: ~1-2 days (state machine + tests)
- A094: ~1 day (decision logic + tests)
- A095: ~1-2 days (rendering + tests)
- **Total**: ~5-8 days wall time for complete sequence

---

## Questions to Resolve Before Starting

1. **Goal model source for A092**: Will orchestrator pass it to compiler?
2. **Quarantine TTL for A093**: Is 5 steps correct, or should it be configurable?
3. **Exhaustion trigger for A094**: Early stop or reclassification downstream?
4. **Compression start for A095**: Step 2 (current assumption) or step 1?

---

**Status**: Exploration complete. Ready for implementation.
