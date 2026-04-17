# Plan for B186 — Trajectory Evaluator

## Card Metadata

- **Card ID**: B186
- **Priority**: P2
- **Dependencies**: None

## Summary

Offline analysis of `agent_execution_trace.json` that scores trajectory quality across 5 dimensions. No LLM needed — pure algorithmic analysis. Produces a TrajectoryScore (0-100) per puzzle.

## Technical Approach

### Step 1: Create benchmarks/arc3/trajectory_eval.py

```python
@dataclass
class TrajectoryScore:
    action_diversity: int        # 0-20
    hypothesis_convergence: int  # 0-20
    exploration_efficiency: int  # 0-20
    plan_adherence: int          # 0-20
    escalation_quality: int      # 0-20
    total: int                   # 0-100
    details: dict                # Per-dimension breakdown

class TrajectoryEvaluator:
    def evaluate(self, trace: List[dict], step_history: List[dict]) -> TrajectoryScore:
        ad = self._score_action_diversity(step_history)
        hc = self._score_hypothesis_convergence(trace)
        ee = self._score_exploration_efficiency(trace)
        pa = self._score_plan_adherence(trace, step_history)
        eq = self._score_escalation_quality(trace, step_history)
        return TrajectoryScore(ad, hc, ee, pa, eq, ad+hc+ee+pa+eq, {...})
```

### Step 2: Scoring functions

**Action diversity** (0-20):
```python
def _score_action_diversity(self, history):
    actions = [s["action_id"] for s in history if "action_id" in s]
    available = history[-1].get("available_actions", []) if history else []
    if not available: return 10  # Can't score without available list
    diversity = len(set(actions)) / len(available)
    return min(20, int(diversity * 20))
```

**Hypothesis convergence** (0-20):
```python
def _score_hypothesis_convergence(self, trace):
    # Find archetype_evolution events
    archetypes = [e["details"]["archetype"] for e in trace if e.get("event_type") == "archetype_update"]
    if len(archetypes) < 2: return 10
    # Count changes: fewer changes = more convergence
    changes = sum(1 for i in range(1, len(archetypes)) if archetypes[i] != archetypes[i-1])
    # 0 changes = 20, 1 change = 16, 5+ changes = 0
    return max(0, 20 - changes * 4)
```

**Exploration efficiency** (0-20):
```python
def _score_exploration_efficiency(self, trace):
    frame_hashes = [e["details"].get("frame_hash") for e in trace if "frame_hash" in e.get("details", {})]
    if not frame_hashes: return 10
    novel_ratio = len(set(frame_hashes)) / len(frame_hashes)
    return min(20, int(novel_ratio * 20))
```

**Plan adherence** (0-20):
```python
def _score_plan_adherence(self, trace, history):
    # Compare actions taken vs active chunk's estimated_actions
    chunk_actions = [e for e in trace if "chunk" in e.get("event_type", "")]
    if not chunk_actions: return 10
    # Count actions that matched the plan
    matches = sum(1 for s in history if s.get("followed_plan"))
    return min(20, int(matches / max(len(history), 1) * 20))
```

**Escalation quality** (0-20):
```python
def _score_escalation_quality(self, trace, history):
    escalations = [e for e in trace if "escalation" in e.get("event_type", "")]
    if not escalations: return 15  # No escalation needed = decent
    # Good: escalations triggered after 5-15 no-progress steps
    # Bad: escalations at step 3 (too early) or step 50+ (too late)
    quality = sum(1 for e in escalations if 5 <= e.get("details", {}).get("steps", 0) <= 15)
    return min(20, int(quality / max(len(escalations), 1) * 20))
```

### Step 3: CLI interface

```python
if __name__ == "__main__":
    import sys, json
    trace = json.load(open(sys.argv[1]))
    evaluator = TrajectoryEvaluator()
    score = evaluator.evaluate(trace, trace.get("step_history", []))
    print(json.dumps(asdict(score), indent=2))
```

### Step 4: Integration in runner.py (optional)

After puzzle completes, compute trajectory score and add to result_payload.

### Step 5: Tests

Create `tests/test_b186_trajectory_eval.py`:
1. Test action_diversity: 1 action used → 5/20; all 6 actions → 20/20
2. Test hypothesis_convergence: 0 changes → 20; 5+ changes → 0
3. Test exploration_efficiency: all unique frames → 20; all same → 1
4. Test total score is sum of sub-scores
5. Test empty trace → reasonable defaults (no crash)

## Verification

```bash
pytest tests/test_b186_trajectory_eval.py -v
# Also test on real trace:
python -m benchmarks.arc3.trajectory_eval agent_execution_trace.json
```
