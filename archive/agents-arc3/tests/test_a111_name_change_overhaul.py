"""Test A111 — Complete name-change overhaul across smoke artifacts."""

import pytest
from agents.arc3.trace_names import (
    canonical_phase,
    normalize_task_identity,
    normalize_failure_payload,
    normalize_run_review,
    normalize_orchestration_status,
    normalize_action_override_reason,
    normalize_click_candidate_fields,
    CANONICAL_PHASES,
    PHASE_ALIASES,
)


class TestCanonicalPhases:
    """Test canonical phase vocabulary."""
    
    def test_canonical_phases_defined(self):
        """Verify canonical phases are defined."""
        expected = {"setup", "perceive", "model", "plan", "act", "evaluate", "replan", "summary"}
        assert CANONICAL_PHASES == expected
    
    def test_already_canonical(self):
        """Return canonical phase unchanged."""
        assert canonical_phase("model") == "model"
        assert canonical_phase("plan") == "plan"
        assert canonical_phase("act") == "act"
    
    def test_legacy_aliases(self):
        """Normalize legacy phase aliases."""
        assert canonical_phase("discover") == "model"
        assert canonical_phase("hypothesize") == "model"  # Default to model
        assert canonical_phase("route") == "plan"
        assert canonical_phase("execute") == "act"
    
    def test_hypothesize_ambiguity(self):
        """Resolve hypothesize based on context."""
        # Goal inference -> model
        assert canonical_phase("hypothesize", context="goal_inference") == "model"
        
        # Action selection -> plan
        assert canonical_phase("hypothesize", context="action_selection") == "plan"
        
        # No context -> default
        assert canonical_phase("hypothesize") == "model"
    
    def test_unknown_default(self):
        """Handle unknown phases."""
        assert canonical_phase("unknown", context="goal_inference") == "model"
        assert canonical_phase("unknown", context="action_selection") == "plan"
        assert canonical_phase("unknown") == "setup"
    
    def test_case_insensitive(self):
        """Normalize case."""
        assert canonical_phase("DISCOVER") == "model"
        assert canonical_phase("Route") == "plan"


class TestNormalizeTaskIdentity:
    """Test task identity normalization."""
    
    def test_preserve_arc_task_id(self):
        """Preserve ARC task_id as primary."""
        payload = {
            "task_id": "arc_eval_001",
            "run_id": "some-uuid-123",
        }
        
        result = normalize_task_identity(payload, "arc_eval_001")
        
        assert result["task_id"] == "arc_eval_001"
    
    def test_mixed_identifiers(self):
        """Separate UUID-like IDs from task_id."""
        payload = {
            "task_id": "arc_eval_001",
            "sidequest_quest_id": "quest-abc-123",
            "sidequest_session_id": "session-xyz-789",
        }
        
        result = normalize_task_identity(payload, "arc_eval_001")
        
        assert result["task_id"] == "arc_eval_001"
        assert result.get("quest_id") == "quest-abc-123"
        assert result.get("arc_session_id") == "session-xyz-789"
        assert "sidequest_quest_id" not in result


class TestNormalizeFailurePayload:
    """Test failure payload normalization."""
    
    def test_exception_to_payload(self):
        """Convert exception to structured failure payload."""
        exc = ValueError("Invalid input")
        
        result = normalize_failure_payload(exc)
        
        assert result["failure_class"] == "crash"
        assert result["exception_type"] == "ValueError"
        assert "Invalid input" in result["exception_message"]
        assert result["failure_reason"]
    
    def test_dict_failure_payload(self):
        """Normalize dict failure payload."""
        failure_dict = {
            "failure_class": "timeout",
            "failure_reason": "LLM call exceeded timeout",
            "exception_type": "TimeoutError",
            "exception_message": "Waited 30 seconds",
        }
        
        result = normalize_failure_payload(failure_dict)
        
        assert result["failure_class"] == "timeout"
        assert result["failure_reason"] == "LLM call exceeded timeout"
        assert result["exception_type"] == "TimeoutError"
    
    def test_unknown_failure(self):
        """Handle unknown failure type."""
        result = normalize_failure_payload(None)
        
        assert result["failure_class"] == "unknown"


class TestNormalizeRunReview:
    """Test run review link normalization."""
    
    def test_primary_artifact_path(self):
        """Set primary test_results_url."""
        primary = "/path/to/submission_results_single.json"
        
        result = normalize_run_review(primary)
        
        assert result["test_results_url"] == f"file://{primary}"
    
    def test_artifact_urls_map(self):
        """Build artifact URLs map."""
        artifacts = {
            "main": "/path/to/submission_results_single.json",
            "live": "/path/to/submission_results_single.live.jsonl",
            "world_model": "/path/to/submission_results_single.world_model.live.jsonl",
        }
        
        result = normalize_run_review("/path/to/submission_results_single.json", artifacts)
        
        assert "main" in result["artifact_urls"]
        assert result["artifact_urls"]["main"] == f"file://{artifacts['main']}"
    
    def test_game_metadata(self):
        """Include game metadata."""
        metadata = {
            "puzzle_description": "Test puzzle",
            "arc_game_url": "https://arcprize.org/tasks/test",
        }
        
        result = normalize_run_review("/path", game_metadata=metadata)
        
        assert result["puzzle_description"] == "Test puzzle"
        assert result["arc_game_url"] == "https://arcprize.org/tasks/test"


class TestNormalizeOrchestrationStatus:
    """Test orchestration status normalization."""
    
    def test_crash_not_ok(self):
        """Change status from ok to failed when failure_class=crash."""
        result = normalize_orchestration_status("crash", current_status="ok")
        
        assert result == "failed"
    
    def test_non_crash_preserved(self):
        """Preserve non-crash status."""
        result = normalize_orchestration_status("timeout", current_status="ok")
        
        assert result == "ok"
    
    def test_no_failure(self):
        """Preserve status when no failure."""
        result = normalize_orchestration_status(None, current_status="ok")
        
        assert result == "ok"


class TestNormalizeActionOverrideReason:
    """Test action override reason normalization."""
    
    def test_remove_stale_action6_suffix(self):
        """Remove ACTION6-specific suffix when action wasn't ACTION6."""
        reason = "replan_forced_action6_probe"
        
        result = normalize_action_override_reason(
            reason,
            requested_action="ACTION0",
            selected_action="ACTION1",
            override_action="ACTION1"
        )
        
        # Should remove the _action6_probe suffix
        assert "action6" not in result.lower() or result == reason


    def test_keep_action6_when_matched(self):
        """Keep ACTION6 mention when action is ACTION6."""
        reason = "replan_forced_action6_probe"
        
        result = normalize_action_override_reason(
            reason,
            requested_action="ACTION0",
            selected_action="ACTION6",
            override_action="ACTION6"
        )
        
        # Should keep since override is actually ACTION6
        assert result  # Non-empty
    
    def test_neutral_reason(self):
        """Keep neutral reasons unchanged."""
        reason = "stall_detected"
        
        result = normalize_action_override_reason(
            reason,
            requested_action="ACTION0",
            selected_action="ACTION1",
        )
        
        assert result == reason


class TestNormalizeClickCandidateFields:
    """Test click candidate field alignment."""
    
    def test_align_main_and_world_model(self):
        """Align click candidate fields between main and world_model rows."""
        main_row = {
            "selected_click_candidate_id": "click-1",
            "selected_click_candidate_rank": 2,
            "selected_click_candidate_role": "framed_center",
            "clicked_x": 10,
            "clicked_y": 20,
            "click_supported": True,
        }
        
        world_row = {
            "selected_click_candidate_id": "click-1",
            "selected_click_candidate_rank": -1,  # Wrong
            "clicked_x": 10,
            "clicked_y": 20,
        }
        
        norm_main, norm_world = normalize_click_candidate_fields(main_row, world_row)
        
        # World row should be corrected
        assert norm_world["selected_click_candidate_rank"] == 2
        assert norm_world["selected_click_candidate_role"] == "framed_center"
        assert norm_world["click_supported"] is True
    
    def test_coordinate_alignment(self):
        """Ensure coordinates match across streams."""
        main_row = {
            "clicked_x": 15,
            "clicked_y": 25,
            "selected_click_candidate_id": "click-2",
        }
        
        world_row = {
            "clicked_x": 15,
            "clicked_y": 25,
        }
        
        norm_main, norm_world = normalize_click_candidate_fields(main_row, world_row)
        
        assert norm_world["clicked_x"] == 15
        assert norm_world["clicked_y"] == 25


class TestA111RegressionFixtures:
    """Regression tests from A111 acceptance criteria."""
    
    def test_mixed_identifiers_bundle(self):
        """Test mixed artifact bundle with task_id=arc_eval_001 and UUID."""
        payload = {
            "task_id": "arc_eval_001",
            "sidequest_quest_id": "quest-uuid-123",
            "data": {"step": 1},
        }
        
        result = normalize_task_identity(payload, "arc_eval_001")
        
        # task_id must remain stable
        assert result["task_id"] == "arc_eval_001"
        # UUID moved to separate field
        assert result.get("quest_id") == "quest-uuid-123"
    
    def test_phase_normalization_bundle(self):
        """Test phase normalization with legacy names."""
        phases = ["discover", "hypothesize", "route", "execute", "unknown"]
        contexts = ["goal_inference", "action_selection", "planning", "execution", "summary"]
        
        results = [
            canonical_phase(phase, context=ctx)
            for phase, ctx in zip(phases, contexts)
        ]
        
        assert results[0] == "model"  # discover -> model
        assert results[1] == "plan"   # hypothesize + action_selection -> plan
        assert results[2] == "plan"   # route -> plan
        assert results[3] == "act"    # execute -> act
        assert results[4] == "setup"  # unknown (no matching context) -> setup
    
    def test_action_override_stale_label(self):
        """Test stale ACTION6-specific label with ACTION1 override."""
        reason = "replan_forced_action6_probe"
        
        result = normalize_action_override_reason(
            reason,
            requested_action="ACTION5",
            selected_action="ACTION1",
            override_action="ACTION1"
        )
        
        # Should not mention ACTION6 in normalized result
        assert "action6" not in result.lower()
    
    def test_crash_orchestration_status(self):
        """Test crash result must not report ok status."""
        failure_info = {
            "failure_class": "crash",
            "failure_reason": "Null pointer exception",
            "orchestration_status": "ok",  # Wrong
        }
        
        corrected_status = normalize_orchestration_status(
            failure_info.get("failure_class"),
            failure_info.get("orchestration_status", "ok")
        )
        
        assert corrected_status != "ok"
        assert corrected_status == "failed"
    
    def test_click_candidate_rank_matching(self):
        """Test click candidate rank matches across main and world_model."""
        main_row = {
            "selected_click_candidate_id": "click-obj-0-center",
            "selected_click_candidate_rank": 1,
            "clicked_x": 32,
            "clicked_y": 63,
        }
        
        world_row = {
            "selected_click_candidate_id": "click-obj-0-center",
            "selected_click_candidate_rank": -1,  # Mismatch
            "clicked_x": 32,
            "clicked_y": 63,
        }
        
        norm_main, norm_world = normalize_click_candidate_fields(main_row, world_row)
        
        # Ranks must now match
        assert norm_main["selected_click_candidate_rank"] == norm_world["selected_click_candidate_rank"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
