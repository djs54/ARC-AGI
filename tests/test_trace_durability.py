import json
import os
import pytest
from pathlib import Path

from run_single_puzzle import _atomic_dump_json


def test_atomic_dump_round_trips(tmp_path):
    target = tmp_path / "trace.json"
    payload = [{"step": 1, "event": "start"}, {"step": 2, "event": "end"}]
    _atomic_dump_json(target, payload)
    assert target.exists()
    assert json.loads(target.read_text()) == payload
    # No leftover .tmp file
    assert not (tmp_path / "trace.json.tmp").exists()


def test_atomic_dump_leaves_no_partial_on_exception(tmp_path, monkeypatch):
    target = tmp_path / "trace.json"
    target.write_text('[{"step":0}]')  # pre-existing good file
    original_dump = json.dump

    def raising_dump(*args, **kwargs):
        raise IOError("disk full")

    monkeypatch.setattr(json, "dump", raising_dump)

    with pytest.raises(IOError):
        _atomic_dump_json(target, [{"step": 1}])

    # The prior good content must still be intact
    assert json.loads(target.read_text()) == [{"step": 0}]
    # The .tmp file may exist but must not have replaced the target
    monkeypatch.setattr(json, "dump", original_dump)


def test_atexit_flushes_in_flight_trace(tmp_path, monkeypatch):
    """A022: verify the atexit handler shape — it reads _current_trace_snapshot
    and atomic-dumps it to agent_execution_trace_path."""
    from agents.arc3.runner import DurableARCRunner

    runner = DurableARCRunner.__new__(DurableARCRunner)
    runner.agent_execution_trace_path = tmp_path / "agent_execution_trace.json"
    runner._current_trace_snapshot = [{"step": 7, "event": "crash_before_export"}]
    # Call directly — should write the file atomically
    runner._atexit_flush_trace()
    assert runner.agent_execution_trace_path.exists()
    loaded = json.loads(runner.agent_execution_trace_path.read_text())
    assert loaded == [{"step": 7, "event": "crash_before_export"}]
