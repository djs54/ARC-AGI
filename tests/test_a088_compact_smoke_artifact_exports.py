"""Tests for A088: Compact Smoke Artifact Exports."""

import json
import pytest
from run_single_puzzle import SingleTaskRunner


class TestCompactSmokeArtifactExports:
    """A088: Tests for compact smoke artifact exports."""

    def test_make_final_result_compact_removes_large_fields(self):
        """Compact export should remove large fields like full graph snapshots."""
        large_result = {
            "task_id": "test_task",
            "game_id": "test_game",
            "correct": False,
            "steps": 30,
            "runtime_seconds": 12.5,
            "failure_class": "strategy_exhausted",
            "final_state": "UNFINISHED",
            "solve_phase_summary": {"final_archetype": "race"},
            "evals": {"metric1": 0.5},
            "quality_dimensions": {"reasoning": 0.7},
            "system_monitoring": {"cpu": 45},
            # Large fields to be removed
            "world_model_snapshot": {
                "node_count": 150,
                "edge_count": 120,
                "contradiction_count": 5,
                "demotion_count": 3,
                "nodes": {f"node-{i}": {"label": "State", "props": {}} for i in range(100)},
                "edges": {f"edge-{i}": {"src": "node1", "rel": "CAUSED", "dst": "node2"} for i in range(100)}
            },
            "agent_execution_trace": [{"step": i, "action": "ACTION1"} for i in range(30)],
            "master_timeline": [{"ts": i, "event": "step"} for i in range(30)]
        }
        
        compact = SingleTaskRunner._make_final_result_compact(large_result)
        
        # Should include essential fields
        assert compact["task_id"] == "test_task"
        assert compact["steps"] == 30
        assert compact["failure_class"] == "strategy_exhausted"
        
        # Should have compact summary, not full snapshot
        assert "world_model_summary" in compact or "world_model_snapshot" in compact
        
        # Full node/edge data should not be in compact export (or be summarized)
        if "world_model_snapshot" not in compact or compact.get("world_model_snapshot"):
            summary = compact.get("world_model_summary", compact.get("world_model_snapshot", {}))
            # Summary should have counts but not full nodes/edges
            assert "node_count" in summary or not summary

    def test_make_final_result_preserves_high_signal_fields(self):
        """Compact export should preserve high-signal fields."""
        result = {
            "task_id": "test_task",
            "game_id": "test_game",
            "correct": True,
            "steps": 15,
            "runtime_seconds": 8.3,
            "failure_class": None,
            "final_state": "FINISHED",
            "solve_phase_summary": {"final_archetype": "space"},
            "evals": {"accuracy": 1.0},
            "quality_dimensions": {"efficiency": 0.5},
            "world_model_snapshot": {
                "node_count": 75,
                "edge_count": 60,
                "contradiction_count": 0,
                "demotion_count": 1,
            }
        }
        
        compact = SingleTaskRunner._make_final_result_compact(result)
        
        # Must preserve these high-signal fields
        assert compact["task_id"] == "test_task"
        assert compact["correct"] == True
        assert compact["steps"] == 15
        assert compact["solve_phase_summary"]["final_archetype"] == "space"
        assert compact["evals"]["accuracy"] == 1.0

    def test_make_final_result_adds_artifact_references(self):
        """Compact export should include artifact path references."""
        result = {
            "task_id": "test_task",
            "game_id": "test_game",
            "correct": False,
            "steps": 30,
            "runtime_seconds": 10.0,
            "failure_class": "strategy_exhausted",
            "final_state": "UNFINISHED",
            "has_execution_trace": True,
            "has_timeline": True,
        }
        
        compact = SingleTaskRunner._make_final_result_compact(result)
        
        # Should have artifacts dict with path references
        assert "artifacts" in compact
        assert "agent_execution_trace" in compact["artifacts"]
        assert "master_timeline" in compact["artifacts"]
        assert "world_model_live" in compact["artifacts"]
        # Paths should be strings, not full payloads
        assert isinstance(compact["artifacts"]["agent_execution_trace"], str)
        assert compact["artifacts"]["agent_execution_trace"].endswith(".json")

    def test_compact_result_size_is_small(self):
        """Compact result should be significantly smaller than original."""
        large_result = {
            "task_id": "test_task",
            "game_id": "test_game",
            "correct": False,
            "steps": 30,
            "runtime_seconds": 12.5,
            "failure_class": "strategy_exhausted",
            "final_state": "UNFINISHED",
            "world_model_snapshot": {
                "nodes": {f"node-{i}": {"label": "State", "props": {}} for i in range(500)},
                "edges": {f"edge-{i}": {"src": "n1", "rel": "CAUSED", "dst": "n2", "props": {"weight": 0.5}} for i in range(500)}
            },
            "agent_execution_trace": [{"step": i, "action": "ACTION1", "reasoning": "x" * 100} for i in range(30)],
        }
        
        compact = SingleTaskRunner._make_final_result_compact(large_result)
        
        # Compact version should be much smaller as JSON
        large_json = json.dumps(large_result)
        compact_json = json.dumps(compact)
        
        assert len(compact_json) < len(large_json)
        # Should be significantly smaller (at least 50% reduction)
        assert len(compact_json) < len(large_json) * 0.5

    def test_compact_result_maintains_task_identity(self):
        """Compact result should maintain task identification."""
        result = {
            "task_id": "unique_task_123",
            "game_id": "game_456",
            "correct": True,
            "steps": 20,
        }
        
        compact = SingleTaskRunner._make_final_result_compact(result)
        
        assert compact["task_id"] == "unique_task_123"
        assert compact["game_id"] == "game_456"

    def test_compact_result_handles_missing_fields(self):
        """Compact export should handle missing optional fields gracefully."""
        minimal_result = {
            "task_id": "test_task",
            "steps": 0,
        }
        
        # Should not raise exception
        compact = SingleTaskRunner._make_final_result_compact(minimal_result)
        
        assert compact["task_id"] == "test_task"
        assert compact["steps"] == 0
        # Should have None or empty values, not errors
        assert "solve_phase_summary" in compact or compact.get("failure_class") is None

    def test_compact_result_preserves_world_model_summary_counts(self):
        """Compact export should preserve world model node/edge counts for analysis."""
        result = {
            "task_id": "test_task",
            "world_model_snapshot": {
                "node_count": 175,
                "edge_count": 145,
                "contradiction_count": 8,
                "demotion_count": 2,
                "nodes": {f"n{i}": {} for i in range(175)},
                "edges": {f"e{i}": {} for i in range(145)}
            }
        }
        
        compact = SingleTaskRunner._make_final_result_compact(result)
        
        # Summary should have counts
        summary = compact.get("world_model_summary", {})
        assert summary.get("node_count") == 175
        assert summary.get("edge_count") == 145
        assert summary.get("contradiction_count") == 8

    def test_compact_result_never_embeds_full_world_model_graph(self):
        """Final compact rows should not retain raw node/edge payloads."""
        result = {
            "task_id": "test_task",
            "world_model_snapshot": {
                "node_count": 2,
                "edge_count": 1,
                "nodes": {"n1": {"label": "State"}, "n2": {"label": "Action"}},
                "edges": {"e1": {"src": "n1", "dst": "n2"}},
            },
        }

        compact = SingleTaskRunner._make_final_result_compact(result)

        assert "world_model_snapshot" not in compact
        assert "nodes" not in compact.get("world_model_summary", {})
        assert "edges" not in compact.get("world_model_summary", {})

    def test_export_results_uses_compact_rows_for_live_world_model_smoke(self, tmp_path):
        """The final JSON file, not only the live stream, should use compact rows."""
        runner = object.__new__(SingleTaskRunner)
        runner.live_smoke = True
        runner.world_model_eval = True
        runner.final_output_path = tmp_path / "submission_results_single.json"
        runner.live_output_path = tmp_path / "submission_results_single.live.jsonl"
        runner.world_model_live_output_path = tmp_path / "submission_results_single.world_model.live.jsonl"
        runner.arc_server_output_path = tmp_path / "submission_results_arcServer.json"
        runner.agent_execution_trace_path = tmp_path / "agent_execution_trace.json"
        runner.master_timeline_path = tmp_path / "master_timeline.json"
        runner.results = [
            {
                "task_id": "test_task",
                "metadata": {},
                "world_model_snapshot": {
                    "node_count": 1,
                    "edge_count": 0,
                    "nodes": {"n1": {"payload": "x" * 1000}},
                    "edges": {},
                },
                "agent_execution_trace": [{"prompt": "x" * 1000}],
                "arc_event_timeline": [],
                "arc_server_responses": [],
                "sidequests_ledger": [],
                "chronological_log": [],
            }
        ]

        runner.export_results()

        exported = json.loads(runner.final_output_path.read_text())
        assert "world_model_summary" in exported[0]
        assert "world_model_snapshot" not in exported[0]
        assert "agent_execution_trace" not in exported[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
