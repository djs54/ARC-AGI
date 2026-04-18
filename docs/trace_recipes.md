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
