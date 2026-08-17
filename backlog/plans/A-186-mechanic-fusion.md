# Plan: A186 — Fuse Cross-Game Rules Into Aggregate Mechanic Records

## Context

A179 shipped structural-fingerprint transfer: `compute_fingerprint(action_family, magnitude_class)` retrieves individual rules from other games whose transition shape matches the current game's. That retrieval is real and live (confirmed cross-`task_id` in production 2026-08-08), but it returns rules one at a time — nothing groups the rules that keep matching each other into a single reusable `Mechanic` record, and nothing checks whether they actually share preconditions or just happen to share a fingerprint.

This plan adds a fusion pass on top of A179, following the `codejunkie99/graph-engineering` skill's three-step fusion pipeline (blocking → matching → merge), applied conservatively: an incorrect merge silently combines two unrelated mechanics' entire evidence trails, so every ambiguous case must stay unmerged rather than guess.

## Implementation

### 1. Blocking (`mechanic_fusion.py` or an addition to `rule_extraction.py`)

Reuse A179's existing fingerprint (`StructuralFingerprint(action_family, magnitude_class)`) as the blocking key — do not invent a new one. A `block()` function takes a list of transferred-rule records (the shape `fetch_transferred_rules` already returns, extended with whatever precondition/entity-history fields step 2 needs) and groups them by fingerprint key. Pure function, no I/O.

```python
def block_by_fingerprint(rules: Sequence[TransferredRuleRecord]) -> dict[str, list[TransferredRuleRecord]]:
    ...
```

### 2. Structure-layer matching

For each block, pull the precondition evidence for each member rule from A176's persisted transition/entity-history (`fetch_entity_history` / whatever `record_transition` populates — check `graph_queries.py`'s current A176 methods before assuming field names). Score pairs within a block:

- **Confident match**: N or more shared precondition features (pin N in code — start at 3, matching the skill's structure-layer example of "3 coauthors and an affiliation"; document the choice and make it a named constant so it's easy to tune later).
- **Ambiguous**: some shared, some conflicting preconditions — do NOT merge; keep as separate candidate `Mechanic`s.
- **No match**: treat as unrelated despite sharing a fingerprint (this is the case A179's own review flagged — same fingerprint, different mechanic).

```python
def match_within_block(block: list[TransferredRuleRecord]) -> MatchResult:
    # returns confident-match clusters + ambiguous leftovers, never force-merges
    ...
```

### 3. Merge policy (deterministic, no LLM call)

For each confident-match cluster, produce/update one `Mechanic` record:
- union member `rule_id`s
- carry `source_game_id` provenance per member (do not collapse to one game)
- aggregate confidence = a deterministic function of member count + member confidences (define explicitly in code, e.g. weighted average capped below any single member's max — do not let fusion count as stronger evidence than the strongest single contributing rule)
- record `merged_from` (the member rule_ids) for auditability

Ambiguous clusters get recorded as separate `Mechanic` candidates (not merged into any existing one), so a future pass with more evidence can resolve them later.

### 4. Server methods (`graph_queries.py`)

Follow A179's `fetch_transferred_rules` pattern exactly (same class, same degrade-on-`capability_missing` behavior):

```python
def record_mechanic_fusion(self, mechanic: MechanicFusionResult) -> dict[str, Any]: ...
def fetch_mechanic_candidates(self, fingerprint_key: str) -> list[dict[str, Any]]: ...
```

Both call `self._call_tool(...)` the same way existing methods do; both return an empty/no-op result on `capability_missing` rather than raising, matching every other A175-A179 method.

### 5. Consumption (`goal_resolver.py`)

Locate A179's transfer-boost application (`_tier_one_hypotheses`, tagged `"entity_history:transfer_match"`, weighted `× 0.05` relative to the in-game `0.08` flat boost — confirm exact constants in the current file before writing code). Add a `Mechanic`-derived boost applied only when `fetch_mechanic_candidates` returns a confident-match record for the current candidate's fingerprint, weighted strictly below the existing transfer boost (e.g. half of it — pin the exact constant when implementing and state it in the PR description). Tag it distinctly (e.g. `"entity_history:mechanic_fusion"`) so it's traceable in telemetry same as the other tags.

## Tests

New `tests/test_a186_mechanic_fusion.py`:

1. **Blocking**: rules with matching fingerprints land in the same block; rules with different `action_family` or `magnitude_class` never share a block.
2. **Matching — confident**: two rules with 3+ shared precondition features (synthetic entity-history fixtures) score as a confident match.
3. **Matching — ambiguous**: two rules with the same fingerprint but disjoint/conflicting preconditions do NOT produce a confident match (regression guard against over-merging on fingerprint alone).
4. **Merge — deterministic**: given a confident-match cluster, the merge output unions `rule_id`s, preserves per-member `source_game_id`, and its aggregate confidence never exceeds the strongest member's confidence.
5. **Merge — ambiguous stays unmerged**: an ambiguous cluster produces separate candidate records, not one fused `Mechanic`.
6. **`fetch_mechanic_candidates` degradation**: `capability_missing` or malformed response returns `[]`, no exception (same shape as `test_a179_structural_transfer.py`'s degradation tests).
7. **Confidence ordering regression**: extend A179's `0 < transfer_boost < in_game_boost` test to `0 < mechanic_boost < transfer_boost < in_game_boost` using the actual constants from `goal_resolver.py`.
8. **A164 scoping regression**: blocking/matching only operates on rules that arrived through `fetch_transferred_rules` (i.e., never reads raw cross-game evidence directly) — assert no direct cross-`game_id` graph read is introduced.

## Verify

```bash
.venv/bin/python -m pytest tests/test_a186_mechanic_fusion.py -v
make test-a
make test-all
```

Live confirmation: same approach as A179 — if the server-side `Mechanic` write/read tools don't exist yet, run a direct MCP round-trip script (`record_mechanic_fusion` / `fetch_mechanic_candidates`) against the real server and confirm clean `capability_missing` degradation, matching A179's precedent. If hippocampy has since added the tools, run `--live-smoke` across two structurally-similar games in one session and confirm a `Mechanic` record fuses rules from both.

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/mechanic_fusion.py` (new) or `agents/arc4/rule_extraction.py` (extended) | Blocking, structure-layer matching, deterministic merge policy |
| `agents/arc4/graph_queries.py` | `record_mechanic_fusion`, `fetch_mechanic_candidates` |
| `agents/arc4/goal_resolver.py` | Consume fused `Mechanic` confidence as a new, distinctly-weighted prior |
| `docs/handoff/B278-mechanic-fusion.md` (new) | Fusion scheme + server-side tool ask for hippocampy |
| `tests/test_a186_mechanic_fusion.py` (new) | Tests above |

## Risks

- **Over-merging.** The single biggest risk per the graph-engineering fusion material: an erroneous merge combines two unrelated mechanics' entire evidence trails silently. The confident-match threshold (N shared preconditions) must be conservative; when in doubt, leave clusters separate rather than tune the threshold down to get more merges.
- **Depends on A176 field shapes.** Precondition matching assumes A176's entity-history/transition records expose enough structure to compare preconditions meaningfully. Check the actual current shape of `fetch_entity_history`'s return value before implementing step 2 — the plan's field names are illustrative, not guaranteed to match the live code.
- **Speculative value until proven live.** Like A179, this is only as good as the rules being fused. If A179's live transfer rate is still low in practice, this card's fusion layer will have little to fuse — check current transfer-hit telemetry before prioritizing this card highly relative to other open work.
