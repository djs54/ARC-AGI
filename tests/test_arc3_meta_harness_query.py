import pytest
from benchmarks.arc3.model_eval import MetaHarnessQuerySurface

def test_list_top_candidates():
    summaries = [
        {"harness_candidate_id": "v1", "solve_rate": 40.0, "avg_tokens_per_step": 200},
        {"harness_candidate_id": "v2", "solve_rate": 50.0, "avg_tokens_per_step": 150},
        {"harness_candidate_id": "v3", "solve_rate": 45.0, "avg_tokens_per_step": 180},
    ]
    
    # Sort by solve_rate (descending)
    top = MetaHarnessQuerySurface.list_top_candidates(summaries, metric="solve_rate", limit=2)
    assert len(top) == 2
    assert top[0]["harness_candidate_id"] == "v2"
    assert top[1]["harness_candidate_id"] == "v3"

    # Sort by avg_tokens_per_step (ascending)
    top_tokens = MetaHarnessQuerySurface.list_top_candidates(summaries, metric="avg_tokens_per_step", limit=2)
    assert top_tokens[0]["harness_candidate_id"] == "v2"
    assert top_tokens[1]["harness_candidate_id"] == "v3"

def test_compare_candidates():
    baseline = {"harness_candidate_id": "v1", "solve_rate": 40.0, "avg_tokens_per_step": 200}
    candidate = {"harness_candidate_id": "v2", "solve_rate": 50.0, "avg_tokens_per_step": 150}
    
    report = MetaHarnessQuerySurface.compare_candidates(baseline, candidate)
    assert report["baseline_id"] == "v1"
    assert report["candidate_id"] == "v2"
    assert report["deltas"]["solve_rate"] == 10.0
    assert report["deltas"]["avg_tokens_per_step"] == -50.0
    assert report["improvement"] is True

def test_list_failure_clusters():
    results = [
        {"task_id": "t1", "correct": True},
        {"task_id": "t2", "correct": False, "final_state": "LOOP"},
        {"task_id": "t3", "correct": False, "final_state": "OOM"},
        {"task_id": "t4", "correct": False, "final_state": "LOOP"},
    ]
    
    clusters = MetaHarnessQuerySurface.list_failure_clusters(results)
    assert len(clusters["LOOP"]) == 2
    assert "t2" in clusters["LOOP"]
    assert "t4" in clusters["LOOP"]
    assert len(clusters["OOM"]) == 1
    assert "t3" in clusters["OOM"]

def test_list_regressions():
    baseline_results = [
        {"task_id": "t1", "correct": True},
        {"task_id": "t2", "correct": True},
        {"task_id": "t3", "correct": False},
    ]
    candidate_results = [
        {"task_id": "t1", "correct": True},
        {"task_id": "t2", "correct": False}, # Regression
        {"task_id": "t3", "correct": True},  # Improvement, not regression
    ]
    
    regressions = MetaHarnessQuerySurface.list_regressions(baseline_results, candidate_results)
    assert regressions == ["t2"]
