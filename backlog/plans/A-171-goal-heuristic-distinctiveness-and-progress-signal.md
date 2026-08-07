# Plan: A171 — Rank Goal Hypotheses by Distinctiveness Instead of Raster-Scan Position

## Context

`goal_resolver.py::_tier_one_hypotheses` takes `perception.entities[:3]` (raster-scan order) with confidence assigned by list index. `plan_generator.py::_click_targets` already successfully ranks click targets by "small, distinct object" heuristics — this plan ports that same principle to goal selection, which currently has no equivalent.

## Implementation

### 1. Distinctiveness scoring helper

In `agents/arc4/goal_resolver.py`, add a ranking function mirroring `_click_targets`'s `color_counts` approach:

```python
@staticmethod
def _distinctiveness_score(entity: PerceivedEntity, color_counts: Mapping[str, int]) -> float:
    rarity = 1.0 / max(color_counts.get(entity.value, 1), 1)
    smallness = 1.0 - min(entity.attributes.get("coverage", 0.0), 1.0)
    return (0.6 * rarity) + (0.4 * smallness)
```

(Weighting is a starting point, not sacred — tunable if live-smoke shows a better balance.)

### 2. Rework `_tier_one_hypotheses`

Replace:

```python
for index, entity in enumerate(perception.entities[:3]):
    ...
    confidence = min(0.75, self._limits.min_heuristic_confidence + 0.12 + (0.05 * (2 - min(index, 2))))
```

with a pre-ranking pass:

```python
color_counts: dict[str, int] = {}
for entity in perception.entities:
    color_counts[entity.value] = color_counts.get(entity.value, 0) + 1

ranked_entities = sorted(
    perception.entities,
    key=lambda e: self._distinctiveness_score(e, color_counts),
    reverse=True,
)[:3]

for index, entity in enumerate(ranked_entities):
    ...
    score = self._distinctiveness_score(entity, color_counts)
    confidence = min(0.75, self._limits.min_heuristic_confidence + 0.12 + (0.05 * min(score, 1.0)))
```

Keep the existing confidence *scale*/cap (`min(0.75, ...)`) — only the ranking input and per-entity confidence driver change, not the overall confidence range other code depends on.

## Tests

New `tests/test_a171_goal_distinctiveness_ranking.py`:

1. `test_rare_small_entity_ranks_first_despite_late_scan_position` — construct a `PerceptionSnapshot` with entities in an order where a large, common-colored entity comes first in the list and a small, unique-colored one comes last — assert the tier-1 hypotheses put the small/rare one first.
2. `test_distinctiveness_score_favors_rarity_and_smallness` — direct unit test of `_distinctiveness_score` with known inputs.
3. `test_confidence_scale_unchanged` — regression guard: max confidence still caps at 0.75, matching pre-existing behavior other code (grounding gate, etc.) depends on.
4. Re-run any existing goal_resolver tests that assumed scan-order behavior — update fixtures if they hardcoded an expected `entity_index`/ordering assumption (check `tests/test_arc4_goal_resolver.py` first).

## Verify

```bash
.venv/bin/python -m pytest tests/test_a171_goal_distinctiveness_ranking.py tests/test_arc4_goal_resolver.py -v
make test-a
make test-all
```

Live confirmation: run `--live-smoke` on a game with heterogeneous entity sizes/colors, compare the selected goal's `entity_index`/attributes against what raster-scan order would have produced (log or reason from the perception snapshot's entity list order vs. selected goal).

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/goal_resolver.py` | New `_distinctiveness_score`; `_tier_one_hypotheses` reworked to rank by it |
| `tests/test_a171_goal_distinctiveness_ranking.py` | New, 3+ tests |
| `tests/test_arc4_goal_resolver.py` | Updated if any existing test assumed scan-order ranking |

## Risks

- Changes which goal gets selected by default — this is the intended effect, but means goal-selection behavior shifts for every game, not just ones where it obviously helps. Live-smoke confirmation should span more than one game before considering this settled.
- Stretch goal (progress-signal correlation) explicitly out of scope if it doesn't fit cleanly — don't force new `WorkflowState` tracking machinery in under time pressure; a follow-up card is a fine outcome.
