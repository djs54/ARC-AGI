# ARC_AGI trace recipes

Canonical jq recipes for `agent_execution_trace.json` and the sibling JSONL
outputs produced by `run_single_puzzle.py`. All recipes assume you run them
from the repo root and that the listed file exists from a recent smoke.

## 1. Every REPLAN route and why it went there (A017)

```sh
jq '.[ ]
    | select(.event_type == "phase_transition" and .metadata.reason == "replan_exit")
    | {step: .step, to: .metadata.target_phase, why: .metadata.route_reason}' \
  agent_execution_trace.json
```

## 2. Plateau family churn — distinct locked families in order (A018)

```sh
jq '[.[ ]
      | select(.event_type == "solve_plateau_detection")
      | .metadata.locked] | unique' \
  agent_execution_trace.json
```

## 3. Distinct plan_ids across the run — upper bound on chunk churn

```sh
jq '[.[ ]
      | select(.tool == "register_plan")
      | .result.plan_id] | unique | length' \
  agent_execution_trace.json
```

## 4. Phase violations — count and offending phases (A014)

```sh
jq '[.[ ]
      | select(.event_type == "phase_violation")] | length' \
  agent_execution_trace.json
```

## 5. Coverage-saturation signal — step at which saturation first fires (A010/A015)

```sh
jq 'first(.[ ]
           | select(.event_type == "graduation_assessment"
                    and (.metadata.reason | test("coverage_saturated")))
           | .step)' \
  agent_execution_trace.json
```

## 6. Per-step reward ticks — where the agent actually made progress

```sh
jq '.[ ]
    | select(.event_type == "action_outcome" and (.metadata.score_delta // 0) > 0)
    | {step: .step, action: .metadata.action_id, score_delta: .metadata.score_delta}' \
  submission_results_single.live.jsonl
```
# Trace Recipes for ARC-AGI

Common `jq` patterns for analyzing `agent_execution_trace.json`.

### 1. REPLAN Routes
Identify all paths taken out of a REPLAN phase:
```bash
jq -r '.[] | select(.event_type == "replan_exit") | [.operation, .metadata.route_reason] | @tsv' agent_execution_trace.json
```

### 2. Plateau Churn
Count locked plateau families by step:
```bash
jq -r '.[] | select(.event_type == "solve_plateau_detection") | [.details.step, .details.locked] | @tsv' agent_execution_trace.json
```

### 3. Plan-ID Distinct Count
Count unique plan registration events:
```bash
jq -r '.[] | select(.event_type == "plan_registration") | .details.plan_id' agent_execution_trace.json | sort | uniq | wc -l
```

### 4. Phase Violations
Detect phase transitions that violate the allowed graph:
```bash
jq -r '.[] | select(.event_type == "phase_transition") | select(.metadata.reason == "violation") | [.details.from_phase, .details.to_phase] | @tsv' agent_execution_trace.json
```

### 5. Coverage Saturation Detection
Identify when the solver hits saturation:
```bash
jq -r '.[] | select(.event_type == "solve_graduation_assessment") | select(.result.status == "saturated") | .details.step' agent_execution_trace.json
```

### 6. Reward Ticks
Identify steps where progress was made:
```bash
jq -r '.[] | select(.details.score_delta > 0 or .details.reward > 0) | [.details.step, .details.score_delta, .details.reward] | @tsv' agent_execution_trace.json
```
