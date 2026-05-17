# A092-A095 Exploration Index

**Date**: May 6, 2026  
**Status**: Complete  
**Baseline**: Green (make test-a passes)

---

## 📚 Documents Created

### 1. **[A092-A095_EXPLORATION_SUMMARY.md](A092-A095_EXPLORATION_SUMMARY.md)** ⭐ START HERE
   - Executive summary of all findings
   - Architecture overview
   - Integration architecture (data flow)
   - Implementation sequence
   - Known risks & mitigations
   - ~1200 lines

### 2. **[A092-A095_QUICK_START.md](A092-A095_QUICK_START.md)** ⭐ THEN READ THIS
   - 60-second card summaries
   - Step-by-step guide for each card (A092 → A095)
   - Copy-paste code templates
   - Critical gotchas
   - Success criteria checklist
   - ~400 lines

### 3. **[A092-A095_CODEBASE_SURVEY.md](A092-A095_CODEBASE_SURVEY.md)** FOR DEEP DIVES
   - Full architectural survey (1800 lines)
   - Current state analysis
   - Integration points for each card
   - Extension seams clearly marked
   - Telemetry integration details
   - Critical blockers identified

### 4. **[A092-A095_IMPLEMENTATION_CHECKLIST.md](A092-A095_IMPLEMENTATION_CHECKLIST.md)** FOR TRACKING
   - Method-by-method breakdown (400 lines)
   - Files to create/modify
   - Acceptance criteria for each card
   - Config changes needed
   - Test file template patterns

### 5. **[A092-A095_CODE_PATTERNS.md](A092-A095_CODE_PATTERNS.md)** FOR REFERENCE
   - Actual code snippets (600 lines)
   - How to extend each pattern
   - Telemetry integration examples
   - Common mistakes to avoid
   - Testing utilities

---

## 🗺️ Navigation Map

### For Project Managers / Leads
1. Read: [EXPLORATION_SUMMARY.md](A092-A095_EXPLORATION_SUMMARY.md) (10 min)
2. Review: Implementation Sequence section
3. Share: Dependency matrix & risk table
4. Plan: Timeline ~5-8 days based on phases

### For Developers Starting Implementation
1. Read: [QUICK_START.md](A092-A095_QUICK_START.md) (15 min)
2. Start with: A092 section (code templates provided)
3. Reference: [CODE_PATTERNS.md](A092-A095_CODE_PATTERNS.md) for actual patterns
4. Track: [IMPLEMENTATION_CHECKLIST.md](A092-A095_IMPLEMENTATION_CHECKLIST.md) as you go

### For Code Reviewers
1. Reference: [CODEBASE_SURVEY.md](A092-A095_CODEBASE_SURVEY.md) Section 5-7 (current state)
2. Check: [CODE_PATTERNS.md](A092-A095_CODE_PATTERNS.md) common mistakes section
3. Validate: Against [IMPLEMENTATION_CHECKLIST.md](A092-A095_IMPLEMENTATION_CHECKLIST.md) acceptance criteria

### For Integration & Testing
1. Review: Test patterns in [QUICK_START.md](A092-A095_QUICK_START.md)
2. Reference: Telemetry fields in [IMPLEMENTATION_CHECKLIST.md](A092-A095_IMPLEMENTATION_CHECKLIST.md)
3. Check: Acceptance criteria in [IMPLEMENTATION_CHECKLIST.md](A092-A095_IMPLEMENTATION_CHECKLIST.md)

---

## 🎯 The Four Cards: Quick Cheat Sheet

| Card | Goal | Key Integration | Blocker | Est. Time |
|------|------|-----------------|---------|-----------|
| **A092** | Terminal-aligned progress scoring | Classifier in compiler, ranking uses tier | Goal model access | 2-3 days |
| **A093** | Prediction falsification + quarantine | Miss recording in orchestrator, suppress in planner | High-conf threshold (0.75) | 1-2 days |
| **A094** | Multi-action exhaustion decision | Exhaustion check in controller, emit decision | Delayed-effect guard logic | 1 day |
| **A095** | Prompt compression | Delta computation + rendering | Required blocks definition | 1-2 days |

---

## 📊 Current State Summary

### ✅ What Already Exists (A073-A090)
- Graph infrastructure with causal edges
- Effect recording (Action→Effect→Observation)
- Prediction generation (string + confidence)
- Falsification conditions (string rules)
- Ranking framework (evidence tier + gain)
- Decision framework (ReasoningMode enum)
- Prompt packet system (ordered blocks)
- Telemetry snapshot emission (JSONL)

### ❌ What's Missing (Blockers for A092-A095)
| A# | Missing | Impact |
|----|---------|--------|
| 092 | Goal distance computation | Can't classify effects |
| 092 | Alignment classification | Can't rank by alignment |
| 093 | Prediction miss edge | Can't track falsification |
| 093 | Quarantine state | Can't suppress actions |
| 094 | Exhaustion check | Can't detect futility |
| 094 | Decision emission | Decision never sent |
| 095 | Delta computation | Can't compress |
| 095 | Compressed rendering | Prompt not optimized |

---

## 🔄 Data Flow Overview

```
Execution Layer:
  orchestrator._execute_action()
    ↓ records
  world_model.record_effect(action, effect, obs)
    ↓ [A092] aligns
  effect.props["alignment_class"]

Decision Layer:
  reasoning_controller.decide()
    ↓ [A093] reads
  world_model.is_action_quarantined()
    ↓ [A094] checks
  world_model.check_multi_action_exhaustion()
    ↓ emits
  ReasoningDecision.world_model_decision

Presentation Layer:
  orchestrator._build_prompt()
    ↓ [A095] reads
  world_model.get_delta_since()
    ↓ builds
  PromptPacket(compressed_blocks)
```

---

## 📋 Dependency Graph

```
Foundation (A073-A090) — WORKING
  ├─ A073 (graph)
  ├─ A074 (compiler)
  ├─ A085 (churn gate)
  ├─ A086 (prediction/falsification)
  ├─ A089 (graph predictions)
  └─ A090 (prior ranking)
      ↓
A092 (terminal alignment) — BLOCKER FOR A093
  ├─ compute_goal_distance()
  ├─ classify_effect_alignment()
  └─ ranking uses alignment_tier
      ↓
A093 (falsification) — BLOCKER FOR A094
  ├─ record_prediction_miss()
  ├─ quarantine_action()
  └─ planner reads quarantine
      ↓
A094 (exhaustion decision) — BLOCKER FOR A095
  ├─ check_multi_action_exhaustion()
  └─ emit world_model_decision
      ↓
A095 (compression) — P1 (can run in parallel after A094)
  ├─ get_delta_since()
  └─ render compressed packet
```

---

## 🚀 Quick Links by Role

**I'm the tech lead:**
→ Read [EXPLORATION_SUMMARY.md](A092-A095_EXPLORATION_SUMMARY.md) + Integration Architecture section

**I'm implementing A092:**
→ Read [QUICK_START.md](A092-A095_QUICK_START.md) A092 section + copy code templates

**I'm reviewing A092 PR:**
→ Check [IMPLEMENTATION_CHECKLIST.md](A092-A095_IMPLEMENTATION_CHECKLIST.md) acceptance criteria

**I need to understand current code:**
→ Read [CODEBASE_SURVEY.md](A092-A095_CODEBASE_SURVEY.md) sections 1-5

**I'm debugging an issue:**
→ Check [CODE_PATTERNS.md](A092-A095_CODE_PATTERNS.md) Common Mistakes section

**I need config changes:**
→ Look at [IMPLEMENTATION_CHECKLIST.md](A092-A095_IMPLEMENTATION_CHECKLIST.md) Config Schema section

---

## 🎓 Learning Path

### Beginner (New to codebase)
1. [QUICK_START.md](A092-A095_QUICK_START.md) — 15 minutes
2. [A092-A095_QUICK_START.md](A092-A095_QUICK_START.md) Code Templates — 10 minutes
3. [CODE_PATTERNS.md](A092-A095_CODE_PATTERNS.md) How Effects Are Recorded — 10 minutes
4. Start implementing A092 test first

### Intermediate (Familiar with A073-A090)
1. [CODEBASE_SURVEY.md](A092-A095_CODEBASE_SURVEY.md) sections 1-5 — 30 minutes
2. [IMPLEMENTATION_CHECKLIST.md](A092-A095_IMPLEMENTATION_CHECKLIST.md) — 20 minutes
3. [CODE_PATTERNS.md](A092-A095_CODE_PATTERNS.md) all patterns — 30 minutes
4. Ready to implement full sequence

### Advanced (Contributing architecture decisions)
1. [EXPLORATION_SUMMARY.md](A092-A095_EXPLORATION_SUMMARY.md) full document — 45 minutes
2. [CODEBASE_SURVEY.md](A092-A095_CODEBASE_SURVEY.md) Critical Decisions section — 15 minutes
3. Review with team; make decisions on blocking questions

---

## ❓ FAQ

**Q: Can I start with A093 instead of A092?**  
A: No. A093 (quarantine) depends on A092 (alignment) for effect classification. Start with A092.

**Q: Can A094 and A095 run in parallel?**  
A: Technically yes, but A094 should finish first since A095 depends on its decision emission for deciding when to compress.

**Q: How do I test my changes?**  
A: Write test file first (TDD). Run: `pytest tests/test_a092_*.py -v && make test-a`

**Q: Where do I find the backlog cards?**  
A: `backlog/A092.md` through `backlog/A095.md` contain requirements.

**Q: What's the config file I need to update?**  
A: Typically `config.yaml` in the working directory. See [IMPLEMENTATION_CHECKLIST.md](A092-A095_IMPLEMENTATION_CHECKLIST.md) Config Schema section.

**Q: Which document has code examples I can copy?**  
A: [CODE_PATTERNS.md](A092-A095_CODE_PATTERNS.md) has actual code snippets for each card.

**Q: How do I know I'm done?**  
A: Check [IMPLEMENTATION_CHECKLIST.md](A092-A095_IMPLEMENTATION_CHECKLIST.md) acceptance criteria for your card.

---

## 📞 Key Contacts & References

**Backlog cards**: `backlog/A092.md` through `backlog/A095.md`  
**Dependency chain**: [EXPLORATION_SUMMARY.md](A092-A095_EXPLORATION_SUMMARY.md) Implementation Sequence section  
**Current baseline**: `make test-a` (green)  
**Smoke test**: `make smoke` (validates end-to-end)

---

## 🎁 What's Included

```
✅ A092-A095_EXPLORATION_SUMMARY.md (executive + architecture + decisions)
✅ A092-A095_QUICK_START.md (copy-paste templates + step-by-step)
✅ A092-A095_CODEBASE_SURVEY.md (full architectural survey)
✅ A092-A095_IMPLEMENTATION_CHECKLIST.md (method checklist + acceptance)
✅ A092-A095_CODE_PATTERNS.md (code examples + patterns)
✅ A092-A095_EXPLORATION_INDEX.md (this file)
✅ Session memory updated: /memories/session/arc_baseline_exploration.md
```

---

## ⏱️ Time Investment

| Document | Read Time | Use During | Total |
|-----------|-----------|-----------|-------|
| EXPLORATION_SUMMARY | 10 min | Planning | 10 min |
| QUICK_START | 15 min | All phases | 15 min |
| CODEBASE_SURVEY | 30 min | Implementation | 30 min |
| IMPLEMENTATION_CHECKLIST | 20 min | Tracking | 20 min |
| CODE_PATTERNS | 30 min | Reference | 30 min |
| **Total**  | | | **105 min (~2 hours)** |

---

## ✨ Next Step

**→ Go read [A092-A095_QUICK_START.md](A092-A095_QUICK_START.md) and start with A092!**

---

Generated: May 6, 2026  
Baseline: ✅ make test-a PASSES  
Status: 🟢 READY FOR IMPLEMENTATION
