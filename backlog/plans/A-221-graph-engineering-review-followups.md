# A221 — Graph-Engineering Review Follow-Ups Implementation Plan

> **For agentic workers:** This plan is intentionally NOT a standard bite-sized TDD task list for Findings 1 and 2 — those require a design decision from the user before any code is written (see `backlog/A221.md`'s "Open question for design" notes). Do not guess at the mechanism and implement it. Findings 3 and 4 ARE fully specified below and can be executed directly.

**Goal:** Close the four follow-ups found by the retroactive `arc-graph-engineering-review` pass over A218/A219/A220 (2026-08-29).

**Architecture:** No new architecture — this either wires an existing signal (`CynefinDomain`) into existing decision logic (`transition()`), promotes existing local-process data (`entity_effects`) to graph state or documents why not, adds a doc note, or runs an existing verification command.

**Tech Stack:** Same as the rest of `agents/arc4/` — no new dependencies.

---

## Finding 4 (do first — cheapest, unblocks nothing else, closes the retroactive-review gap)

### Task 1: Run a live smoke trace and verify Shift-A compliance

**Files:** none modified — verification only.

- [ ] **Step 1: Run the smoke command**

```bash
make smoke
```

Expected: completes without crashing, writes a fresh `artifacts/agent_execution_trace.json` (or the configured trace path).

- [ ] **Step 2: Run the compliance check against the fresh trace**

```bash
PYTHON=.venv/bin/python python scripts/check_compliance_violations.py
```

Expected: exit 0, no `COMPLIANCE VIOLATIONS` printed. If it fails, that's a real Shift-A/B regression somewhere in A218/A219/A220 (or elsewhere) — stop and investigate before touching Findings 1-3, do not paper over it.

- [ ] **Step 3: Record the result**

Add one line to A221.md's Outcome section (once started) noting the trace path used, the command output, and the date run.

---

## Finding 3 (do second — pure documentation, no code)

### Task 2: Document the CHAOTIC-unreachable constraint

**Files:**
- Modify: `backlog/A220.md`'s "Next step" section

- [ ] **Step 1: Add the constraint**

Append to A220.md's existing "Next step" paragraph (do not rewrite the rest of it):

```markdown
**Known constraint for this follow-up (found 2026-08-29, A221 Finding 3):** `metadata["cynefin_domain"]` can never read `"chaotic"` for a candidate that survives to the final candidate list — `plan_generator.py::_build_candidates`'s A208 hard-exclusion (`had_any_record and nothing_live_remains: continue`) drops an all-falsified-evidence candidate before this metadata assignment runs. Design against `"disorder"`/`"converged"`/`"complex"` only; `"chaotic"` is observable only via `classify_domain()`'s own unit tests, never in live candidate metadata.
```

- [ ] **Step 2: Commit**

```bash
git add backlog/A220.md
git commit -m "A221 Finding 3: document CHAOTIC-unreachable constraint in A220's Next step"
```

---

## Findings 1 and 2 (design decisions required — do not implement without them)

### Task 3: Present Finding 1's open question to the user

Before writing any code in `agents/arc4/annatar_state_machine.py`, present the two options from A221.md's Finding 1 ("shorten patience on CHAOTIC" vs. "hard-exclude like A208" vs. "explicitly defer") and get a decision. Once decided, this becomes a normal bite-sized task (likely: one new branch in `transition()`'s multiplier logic, one new `AnnatarLimits` field if a multiplier approach is chosen, tests mirroring A217's `complex_domain_deepening_multiplier` test pattern in `tests/test_a217_domain_aware_anchor_patience.py`).

### Task 4: Present Finding 2's open question to the user

Before touching `agents/arc4/perceive.py` or proposing a graph schema change, get a real live-smoke trace (Task 1 produces one) showing actual `entity_effects` distributions from genuine ARC gameplay, per A219's own Outcome section's stated gap ("I have not run a live smoke test to see what `entity_effects` distributions look like on genuine ARC gameplay"). Present that data to the user alongside the graph-promote-vs-stay-local question before implementing either path.

---

## Self-Review Notes

- Finding 4 and Finding 3 are fully specified, no placeholders, executable immediately.
- Findings 1 and 2 deliberately stop short of a code-level task list — writing one now would mean guessing at a decision that belongs to the user, which is exactly the mistake this session's post-mortem (see `backlog/A221.md`'s Problem section) is trying to avoid repeating.
