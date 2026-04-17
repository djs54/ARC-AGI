"""
Tests for ARC3 Submission Compliance

Verifies that the submission runner and pre-submit check are functioning as expected.
"""

import json
import pytest
from pathlib import Path
from benchmarks.arc3.pre_submit_check import validate_submission, verify_model_budget, verify_offline_mode, verify_output_format

REPO_ROOT = Path(__file__).resolve().parents[1]

def test_model_budget_validation():
    """Ensure model budget check correctly identifies violations."""
    # This should pass with the current model_budget.yaml
    assert verify_model_budget() is True

def test_offline_mode_validation():
    """Ensure offline mode check correctly verifies provider."""
    # This should pass if sidequests.toml is set to 'ollama'
    assert verify_offline_mode() is True

def test_output_format_validation(tmp_path):
    """Test output format validator with various JSON structures."""
    valid_results = [
        {
            "task_id": "test_1",
            "predictions": [[[0, 1], [1, 0]]],
            "confidence": [0.9]
        }
    ]
    
    results_file = tmp_path / "valid_results.json"
    with open(results_file, "w") as f:
        json.dump(valid_results, f)
        
    assert verify_output_format(results_file) is True
    
    invalid_results = [{"task_id": "test_2"}] # Missing predictions/confidence
    invalid_file = tmp_path / "invalid_results.json"
    with open(invalid_file, "w") as f:
        json.dump(invalid_results, f)
        
    assert verify_output_format(invalid_file) is False

def test_submission_runner_initialization():
    """Smoke test for submission runner initialization."""
    from benchmarks.arc3.submission import SubmissionRunner
    runner = SubmissionRunner()
    # We won't fully initialize in tests to avoid DB/LLM side effects
    assert runner.config is not None
    assert runner.tasks == []
