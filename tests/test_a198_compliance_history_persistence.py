"""Tests for A198: Persist Compliance Report Snapshots to a History File."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from scripts.graph_compliance_report import append_history, report, show_history


class TestAppendHistory:
    """Tests 1-3: append_history function behavior."""

    def test_append_history_creates_fresh_file_and_parent_dir(self):
        """Test 1: append_history on a fresh (nonexistent) path creates the file and parent directory, writes exactly one valid JSON line."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = Path(tmpdir) / "subdir" / "deep" / "compliance_history.jsonl"

            report_dict = {
                "total_steps": 10,
                "llm_escalation_rate_goal_per_100": 20.0,
                "llm_escalation_rate_plan_per_100": 30.0,
                "graph_grounded_decision_rate": 80.0,
                "capability_missing_total": 1,
                "compliance_violation_total": 0,
                "exhaustion_source_breakdown": {},
            }
            trace_paths = ["artifacts/agent_execution_trace.json"]

            # Call append_history on nonexistent path
            append_history(report_dict, trace_paths, history_path)

            # Verify file exists
            assert history_path.exists()

            # Verify parent directories were created
            assert history_path.parent.exists()

            # Verify exactly one line was written
            lines = history_path.read_text().strip().split("\n")
            assert len(lines) == 1

            # Verify the line is valid JSON
            row = json.loads(lines[0])
            assert "timestamp" in row
            assert "trace_files" in row
            assert row["trace_files"] == trace_paths
            assert row["total_steps"] == 10

    def test_append_history_twice_preserves_first_row(self):
        """Test 2: Calling append_history twice appends a second line without disturbing the first."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = Path(tmpdir) / "compliance_history.jsonl"

            report1 = {
                "total_steps": 10,
                "llm_escalation_rate_goal_per_100": 20.0,
                "llm_escalation_rate_plan_per_100": 30.0,
                "graph_grounded_decision_rate": 80.0,
                "capability_missing_total": 1,
                "compliance_violation_total": 0,
                "exhaustion_source_breakdown": {},
            }
            trace_paths1 = ["artifacts/agent_execution_trace.json"]

            report2 = {
                "total_steps": 15,
                "llm_escalation_rate_goal_per_100": 25.0,
                "llm_escalation_rate_plan_per_100": 35.0,
                "graph_grounded_decision_rate": 85.0,
                "capability_missing_total": 2,
                "compliance_violation_total": 1,
                "exhaustion_source_breakdown": {"threshold_only": 1},
            }
            trace_paths2 = ["artifacts/agent_execution_trace.json"]

            # Append first report
            append_history(report1, trace_paths1, history_path)

            # Append second report
            append_history(report2, trace_paths2, history_path)

            # Verify exactly two lines
            lines = history_path.read_text().strip().split("\n")
            assert len(lines) == 2

            # Verify both parse as valid JSON
            row1 = json.loads(lines[0])
            row2 = json.loads(lines[1])

            # Verify they are distinct
            assert row1["total_steps"] == 10
            assert row2["total_steps"] == 15

            # Verify both have timestamps
            assert "timestamp" in row1
            assert "timestamp" in row2

    def test_append_history_preserves_report_dict_keys(self):
        """Test 3: Every appended row's trace_files field matches what was passed in, and the rest of the row matches the report dict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = Path(tmpdir) / "compliance_history.jsonl"

            report_dict = {
                "total_steps": 20,
                "llm_escalation_rate_goal_per_100": 15.5,
                "llm_escalation_rate_plan_per_100": 45.25,
                "graph_grounded_decision_rate": 75.0,
                "capability_missing_total": 3,
                "compliance_violation_total": 2,
                "exhaustion_source_breakdown": {"graph_confirmed": 2, "threshold_only": 1},
            }
            trace_paths = ["trace1.json", "trace2.json", "trace3.json"]

            append_history(report_dict, trace_paths, history_path)

            # Read back and verify
            row = json.loads(history_path.read_text().strip())

            # Verify trace_files exactly matches
            assert row["trace_files"] == trace_paths

            # Verify all report keys are present and match
            for key, value in report_dict.items():
                assert key in row
                assert row[key] == value, f"Key {key}: expected {value}, got {row[key]}"


class TestShowHistory:
    """Tests 4-5: show_history function behavior."""

    def test_show_history_nonexistent_file_prints_message(self, capsys):
        """Test 4: show_history on a nonexistent path prints a "no history yet" message and returns cleanly (no exception)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = Path(tmpdir) / "nonexistent.jsonl"

            # Should not raise, should return 0
            result = show_history(history_path, last=None)

            assert result == 0

            # Should print a message about no history
            captured = capsys.readouterr()
            assert "No history yet" in captured.out

    def test_show_history_with_last_n_limit(self, capsys):
        """Test 5: show_history with --last 2 against a 5-row history file only shows the 2 most recent rows, in chronological order."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = Path(tmpdir) / "compliance_history.jsonl"

            # Create 5 rows manually
            rows = []
            for i in range(5):
                row = {
                    "timestamp": f"2024-01-0{i+1}T12:00:00+00:00",
                    "trace_files": [f"trace{i+1}.json"],
                    "total_steps": 10 + i,
                    "llm_escalation_rate_goal_per_100": 20.0 + i,
                    "llm_escalation_rate_plan_per_100": 30.0 + i,
                    "graph_grounded_decision_rate": 80.0 - i,
                    "capability_missing_total": i,
                    "compliance_violation_total": i % 2,
                    "exhaustion_source_breakdown": {},
                }
                rows.append(row)

            # Write all rows
            with history_path.open("w") as f:
                for row in rows:
                    f.write(json.dumps(row) + "\n")

            # Call show_history with last=2
            result = show_history(history_path, last=2)

            assert result == 0

            # Capture output
            captured = capsys.readouterr()
            output_lines = [line for line in captured.out.split("\n") if line.strip()]

            # Should have exactly 2 lines (the last 2 rows)
            assert len(output_lines) == 2

            # Verify they are the last 2 rows (in chronological order)
            assert "2024-01-04" in output_lines[0]  # 4th row
            assert "2024-01-05" in output_lines[1]  # 5th row


class TestScriptIntegration:
    """Tests 6-7: Script-level integration tests."""

    def test_script_without_flags_identical_to_before(self):
        """Test 6: Running the script without --append-history/--show-history produces identical stdout to before this card."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a minimal trace file
            trace_data = [
                {
                    "snapshot_type": "step",
                    "step": 1,
                    "reasoning_escalation_count": 1,
                    "llm_escalated_plan": False,
                    "graph_grounded": True,
                    "capability_missing_count": 0,
                    "compliance_violation_count": 0,
                },
                {
                    "snapshot_type": "step",
                    "step": 2,
                    "reasoning_escalation_count": 0,
                    "llm_escalated_plan": True,
                    "graph_grounded": False,
                    "capability_missing_count": 1,
                    "compliance_violation_count": 0,
                },
            ]

            trace_path = Path(tmpdir) / "trace.json"
            trace_path.write_text(json.dumps(trace_data))

            # Run the script without any new flags
            result = subprocess.run(
                [sys.executable, "scripts/graph_compliance_report.py", str(trace_path)],
                cwd="/Users/djshelton/Desktop/GitProjects/ARC_AGI",
                capture_output=True,
                text=True,
            )

            assert result.returncode == 0

            # Parse the output as JSON
            output_json = json.loads(result.stdout)

            # Verify structure matches expected report
            assert "total_steps" in output_json
            assert output_json["total_steps"] == 2
            assert "llm_escalation_rate_goal_per_100" in output_json
            assert "llm_escalation_rate_plan_per_100" in output_json
            assert "graph_grounded_decision_rate" in output_json
            assert "capability_missing_total" in output_json
            assert "compliance_violation_total" in output_json
            assert "exhaustion_source_breakdown" in output_json

    def test_show_history_with_trace_args_is_mutually_exclusive(self, capsys):
        """Test 7: --show-history and positional trace args together: history mode wins."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a history file
            history_path = Path(tmpdir) / "compliance_history.jsonl"
            row = {
                "timestamp": "2024-01-01T12:00:00+00:00",
                "trace_files": ["trace.json"],
                "total_steps": 10,
                "llm_escalation_rate_goal_per_100": 20.0,
                "llm_escalation_rate_plan_per_100": 30.0,
                "graph_grounded_decision_rate": 80.0,
                "capability_missing_total": 0,
                "compliance_violation_total": 0,
                "exhaustion_source_breakdown": {},
            }
            with history_path.open("w") as f:
                f.write(json.dumps(row) + "\n")

            # Create a trace file
            trace_data = [
                {
                    "snapshot_type": "step",
                    "step": 1,
                    "reasoning_escalation_count": 0,
                    "llm_escalated_plan": False,
                    "graph_grounded": True,
                    "capability_missing_count": 0,
                    "compliance_violation_count": 0,
                }
            ]
            trace_path = Path(tmpdir) / "trace.json"
            trace_path.write_text(json.dumps(trace_data))

            # Run with both --show-history and trace path
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/graph_compliance_report.py",
                    str(trace_path),
                    "--show-history",
                    str(history_path),
                ],
                cwd="/Users/djshelton/Desktop/GitProjects/ARC_AGI",
                capture_output=True,
                text=True,
            )

            assert result.returncode == 0

            # Should print history (history mode wins)
            assert "2024-01-01" in result.stdout
            assert "llm_goal=" in result.stdout

            # Should NOT print the report JSON (would have indentation/structure)
            assert "total_steps" not in result.stdout or "llm_goal=" in result.stdout


class TestHistoryFilePersistence:
    """Integration test: Multiple append calls, then read back."""

    def test_history_file_multirow_roundtrip(self):
        """Integration: append multiple reports across separate calls, re-read them all."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = Path(tmpdir) / "compliance_history.jsonl"

            # Simulate 3 separate runs
            for run in range(1, 4):
                report_dict = {
                    "total_steps": 10 * run,
                    "llm_escalation_rate_goal_per_100": 20.0 + run,
                    "llm_escalation_rate_plan_per_100": 30.0 + run,
                    "graph_grounded_decision_rate": 80.0 - run,
                    "capability_missing_total": run,
                    "compliance_violation_total": run % 2,
                    "exhaustion_source_breakdown": {"test": run},
                }
                trace_paths = [f"artifacts/run_{run}.json"]

                append_history(report_dict, trace_paths, history_path)

            # Read all rows back
            all_rows = []
            with history_path.open() as f:
                for line in f:
                    if line.strip():
                        all_rows.append(json.loads(line))

            # Verify we got 3 rows
            assert len(all_rows) == 3

            # Verify each row is complete and distinct
            for i, row in enumerate(all_rows):
                run = i + 1
                assert row["total_steps"] == 10 * run
                assert row["llm_escalation_rate_goal_per_100"] == 20.0 + run
                assert row["capability_missing_total"] == run
                assert row["trace_files"] == [f"artifacts/run_{run}.json"]
                assert "timestamp" in row
