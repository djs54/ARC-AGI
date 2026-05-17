"""Test A110 — Click outcome evaluation telemetry."""

import pytest
from agents.arc3.click_telemetry import (
    ClickOutcomeTelemetry,
    ClickTelemetryStore,
    extract_click_telemetry_from_step,
)


class TestClickOutcomeTelemetry:
    """Test click outcome telemetry data class."""

    def test_create_click_outcome(self):
        """Create a click outcome telemetry record."""
        telemetry = ClickOutcomeTelemetry(
            step=5,
            action_identity="ACTION6@10,12",
            coordinate_required=True,
            missing_coordinate_click=False,
            click_candidate_id="click-1",
            click_candidate_role="framed_center",
            clicked_x=10,
            clicked_y=12,
            frame_delta=True,
            click_supported=True,
        )
        
        assert telemetry.step == 5
        assert telemetry.action_identity == "ACTION6@10,12"
        assert telemetry.click_supported is True

    def test_to_dict(self):
        """Convert telemetry to dict."""
        telemetry = ClickOutcomeTelemetry(
            step=5,
            action_identity="ACTION6@10,12",
            coordinate_required=True,
            missing_coordinate_click=False,
            click_supported=True,
        )
        
        result = telemetry.to_dict()
        
        assert result["step"] == 5
        assert result["action_identity"] == "ACTION6@10,12"
        assert result["click_supported"] is True


class TestClickTelemetryStore:
    """Test click telemetry store."""

    def test_record_click_outcome(self):
        """Record click outcome in store."""
        store = ClickTelemetryStore()
        
        telemetry = ClickOutcomeTelemetry(
            step=5,
            action_identity="ACTION6@10,12",
            coordinate_required=True,
            missing_coordinate_click=False,
            click_candidate_id="click-1",
            click_supported=True,
        )
        
        store.record_click_outcome(telemetry)
        
        assert len(store.click_outcomes) == 1
        assert "ACTION6@10,12" in store.candidate_tried
        assert "ACTION6@10,12" in store.candidate_supported

    def test_track_falsified_candidates(self):
        """Track falsified candidates."""
        store = ClickTelemetryStore()
        
        telemetry = ClickOutcomeTelemetry(
            step=5,
            action_identity="ACTION6@10,12",
            coordinate_required=True,
            missing_coordinate_click=False,
            click_candidate_id="click-1",
            click_falsified=True,
        )
        
        store.record_click_outcome(telemetry)
        
        assert "ACTION6@10,12" in store.candidate_falsified
        assert "ACTION6@10,12" not in store.candidate_supported

    def test_get_click_summary(self):
        """Get summary of click outcomes."""
        store = ClickTelemetryStore()
        
        # Record several clicks
        for i in range(3):
            telemetry = ClickOutcomeTelemetry(
                step=i,
                action_identity="ACTION6@10,12",
                coordinate_required=True,
                missing_coordinate_click=False,
                click_candidate_id="click-1",
                click_supported=(i == 0),  # First supported
                click_falsified=(i > 0),
            )
            store.record_click_outcome(telemetry)
        
        summary = store.get_click_summary()
        
        assert summary["click_count"] == 3
        assert summary["unique_action_identity_count"] == 1
        assert summary["click_candidates_supported"] == 1
        # All 3 clicks have the same action_identity, so it's 1 candidate tried and 1 candidate falsified
        assert summary["click_candidates_tried"] == 1
        assert summary["click_candidates_falsified"] == 1

    def test_detect_null_click_loop(self):
        """Detect null click loop."""
        store = ClickTelemetryStore()
        
        # Record many null clicks
        for i in range(10):
            telemetry = ClickOutcomeTelemetry(
                step=i,
                action_identity="ACTION6",
                coordinate_required=True,
                missing_coordinate_click=True,  # Missing coordinates
                click_falsified=True,
            )
            store.record_click_outcome(telemetry)
        
        summary = store.get_click_summary()
        
        assert summary["null_click_loop_detected"] is True
        assert summary["missing_coordinate_click_count"] == 10

    def test_failure_message_null_clicks(self):
        """Generate failure message for null clicks."""
        store = ClickTelemetryStore()
        
        # Record null clicks
        for i in range(5):
            telemetry = ClickOutcomeTelemetry(
                step=i,
                action_identity="ACTION6",
                coordinate_required=True,
                missing_coordinate_click=True,
                click_falsified=True,
            )
            store.record_click_outcome(telemetry)
        
        message = store.get_failure_message()
        
        assert message is not None
        assert "null ACTION6 clicks" in message

    def test_failure_message_repeated_clicks(self):
        """Generate failure message for repeated ineffective clicks."""
        store = ClickTelemetryStore()
        
        # Record repeated clicks with no progress
        for i in range(7):
            telemetry = ClickOutcomeTelemetry(
                step=i,
                action_identity="ACTION6@10,12",
                coordinate_required=True,
                missing_coordinate_click=False,
                click_candidate_id="click-1",
                click_falsified=True,  # No effect
            )
            store.record_click_outcome(telemetry)
        
        message = store.get_failure_message()
        
        assert message is not None
        assert "repeated ACTION6@10,12" in message


class TestExtractClickTelemetry:
    """Test telemetry extraction from step records."""

    def test_extract_click_action(self):
        """Extract click action telemetry."""
        step_record = {
            "step": 5,
            "action_id": "ACTION6",
            "action_identity": "ACTION6@10,12",
            "x": 10,
            "y": 12,
            "coordinate_required": True,
            "missing_coordinate_click": False,
            "click_candidate_id": "click-1",
            "click_candidate_role": "framed_center",
            "click_candidate_rank": 1,
        }
        
        telemetry = extract_click_telemetry_from_step(
            step_record,
            frame_hash_before="hash1",
            frame_hash_after="hash2",
            config_hash_before="config1",
            config_hash_after="config2",
        )
        
        assert telemetry is not None
        assert telemetry.step == 5
        assert telemetry.action_identity == "ACTION6@10,12"
        assert telemetry.clicked_x == 10
        assert telemetry.clicked_y == 12

    def test_detect_frame_delta(self):
        """Detect frame change."""
        step_record = {
            "step": 5,
            "action_id": "ACTION6",
            "action_identity": "ACTION6@10,12",
            "x": 10,
            "y": 12,
            "coordinate_required": True,
            "missing_coordinate_click": False,
        }
        
        telemetry = extract_click_telemetry_from_step(
            step_record,
            frame_hash_before="hash1",
            frame_hash_after="hash2",  # Different
        )
        
        assert telemetry.frame_delta is True
        assert telemetry.click_supported is True

    def test_no_delta_falsified(self):
        """No delta means click falsified."""
        step_record = {
            "step": 5,
            "action_id": "ACTION6",
            "action_identity": "ACTION6@10,12",
            "coordinate_required": True,
            "missing_coordinate_click": False,
        }
        
        telemetry = extract_click_telemetry_from_step(
            step_record,
            frame_hash_before="hash1",
            frame_hash_after="hash1",  # Same
            config_hash_before="config1",
            config_hash_after="config1",  # Same
        )
        
        assert telemetry.frame_delta is False
        assert telemetry.config_delta is False
        assert telemetry.click_falsified is True

    def test_skip_non_click_actions(self):
        """Skip non-click actions."""
        step_record = {
            "step": 5,
            "action_id": "ACTION0",
            "action_identity": "ACTION0",
        }
        
        telemetry = extract_click_telemetry_from_step(
            step_record,
            frame_hash_before="hash1",
            frame_hash_after="hash2",
        )
        
        assert telemetry is None

    def test_missing_coordinate_click(self):
        """Track missing coordinate clicks."""
        step_record = {
            "step": 5,
            "action_id": "ACTION6",
            "action_identity": "ACTION6",
            "coordinate_required": True,
            "missing_coordinate_click": True,
        }
        
        telemetry = extract_click_telemetry_from_step(
            step_record,
            frame_hash_before="hash1",
            frame_hash_after="hash1",
        )
        
        assert telemetry.missing_coordinate_click is True


class TestClickTelemetryIntegration:
    """Integration tests for click telemetry."""

    def test_full_click_sequence(self):
        """Process a full click sequence."""
        store = ClickTelemetryStore()
        
        # Simulate a click sequence: 3 clicks, 1 successful, 2 fail
        step_records = [
            {
                "step": 0,
                "action_id": "ACTION6",
                "action_identity": "ACTION6@10,12",
                "coordinate_required": True,
                "missing_coordinate_click": False,
                "click_candidate_id": "click-1",
                "click_candidate_role": "framed_center",
            },
            {
                "step": 1,
                "action_id": "ACTION6",
                "action_identity": "ACTION6@20,25",
                "coordinate_required": True,
                "missing_coordinate_click": False,
                "click_candidate_id": "click-2",
                "click_candidate_role": "mismatch_cell",
            },
            {
                "step": 2,
                "action_id": "ACTION6",
                "action_identity": "ACTION6@10,12",
                "coordinate_required": True,
                "missing_coordinate_click": False,
                "click_candidate_id": "click-1",
            },
        ]
        
        # Hash changes only after first click
        frame_hashes = ["hash0", "hash1", "hash1", "hash1"]
        
        for i, record in enumerate(step_records):
            telemetry = extract_click_telemetry_from_step(
                record,
                frame_hash_before=frame_hashes[i],
                frame_hash_after=frame_hashes[i + 1],
            )
            
            if telemetry:
                store.record_click_outcome(telemetry)
        
        summary = store.get_click_summary()
        
        assert summary["click_count"] == 3
        assert summary["unique_action_identity_count"] == 2
        assert summary["click_candidates_tried"] == 2
        assert summary["click_candidates_supported"] == 1
        assert summary["click_candidates_falsified"] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
