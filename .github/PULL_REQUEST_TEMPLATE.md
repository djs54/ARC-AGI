## Summary

<!-- What changed and why. -->

## Graph-Engineering Review

<!-- Required for any change touching agents/arc4/, arc_runtime/, or
run_single_puzzle.py. Invoke the arc-graph-engineering-review skill and
answer honestly -- see .claude/skills/arc-graph-engineering-review/SKILL.md.
Not applicable to docs-only / backlog-only / CI-config-only PRs; delete this
section for those. -->

- **Shift A** (deterministic phases stay LLM-free):
- **Shift B** (raw results not narrative; single decision owner):
- **Shift C** (graph vs. local state, and is any tradeoff stated not silent):
- **Investigation Loop** (only if this PR is a bug/anomaly fix — did the investigation anchor on an entity, test a hypothesis, log a verdict?):

## Test plan

- [ ] `make test-a` / full suite green
- [ ] <!-- anything else specific to this change -->
