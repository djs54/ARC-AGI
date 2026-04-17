# B-60-arc-submission — Submission Notebook/Runner Assembly + Final Compliance Gate

**Card:** B60 | **Priority:** P12 | **Depends on:** B57, B58, B59 (harness + model + bundle ready)

## Summary
Assemble final submission runner/notebook with automated compliance checks. Acts as single entry point for contest evaluators.

## Technical Approach

### Submission Artifact
Either Jupyter notebook (if supported) or standalone Python script:

```python
# submission.py

def initialize():
    # Offline install/bootstrap
    # - Extract bundle
    # - Verify checksums
    # - Load models
    # - Start Brain Daemon
    
def run_evaluation():
    # Main ARC episode loop
    # - Load task set
    # - For each task: solve with memory-augmented agent
    # - Record step traces
    
def export_results():
    # Generate required output format
    # - Results JSON matching evaluator schema
    # - Traces and logs
    # - Metadata (model version, runtime, etc.)
```

### Compliance Check
```python
# pre_submit_check.py
def validate_submission():
    # 1. Verify offline bundle
    # 2. Check model is within resource budget (B54 checklist)
    # 3. Validate output format against spec
    # 4. Confirm no network calls during run
    # 5. Runtime sanity check (test run on 1 puzzle)
    # Return: pass/fail + detailed report
```

### Output Format
Must match official evaluator expectations:
```json
{
  "task_id": "arc_task_001",
  "predictions": [...],
  "confidence": [...],
  "metadata": {
    "model": "llama3.1:8b",
    "memory_enabled": true,
    "runtime_seconds": 45.2
  }
}
```

## Files to Create/Modify

- `benchmarks/arc3/submission.py` (or `.ipynb`) — submission runner
- `benchmarks/arc3/pre_submit_check.py` — compliance validation
- `benchmarks/arc3/README.md` — evaluator instructions
- `tests/test_submission_compliance.py` — automated checks

## Acceptance Criteria

1. End-to-end run completes within verified runtime budget (B54)
2. No network dependency is exercised (verified by network monitor)
3. Output format matches official specification exactly
4. Pre-submit check passes and blocks non-compliant runs
5. Single command to run: `python submission.py` (no manual steps)
6. Results are reproducible across runs

## Notes

- This is the "face" of SideQuests in the contest
- Must be bulletproof and easy for evaluators to run
- Final validation before submission deadline
