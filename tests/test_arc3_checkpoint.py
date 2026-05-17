"""CheckpointManager tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from benchmarks.ab_harness import ABTask
from agents.arc3.checkpoint import CheckpointManager, RunCheckpoint
from agents.arc3.solver import TerminalGroundedScore


def _sample_tasks() -> list[ABTask]:
    tasks = [
        ABTask(task_id="task-1", category="c", prompt="p1"),
        ABTask(task_id="task-2", category="c", prompt="p2"),
    ]
    for task in tasks:
        setattr(task, "game_id", "game")
    return tasks



def test_load_or_create_creates_all_pending(tmp_path):
    CheckpointManager.CHECKPOINT_DIR = tmp_path
    mgr = CheckpointManager("test")
    tasks = _sample_tasks()
    cp = mgr.load_or_create(tasks)
    assert all(tc.status == "pending" for tc in cp.tasks.values())


def test_load_preserves_completed_tasks(tmp_path):
    CheckpointManager.CHECKPOINT_DIR = tmp_path
    mgr = CheckpointManager("test")
    file_path = mgr.CHECKPOINT_DIR / "arc_run_test.json"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "card_id": "test",
        "tasks": {
            "task-1": {"status": "complete", "plan_id": "p1", "result": {"task_id": "task-1"}, "attempt": 1},
            "task-2": {"status": "pending", "plan_id": None, "result": None, "attempt": 0},
        },
    }
    with file_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    cp = mgr.load_or_create(_sample_tasks())
    assert cp.tasks["task-1"].status == "complete"
    assert cp.tasks["task-2"].status == "pending"


def test_mark_complete_persists(tmp_path):
    CheckpointManager.CHECKPOINT_DIR = tmp_path
    mgr = CheckpointManager("test")
    cp = mgr.load_or_create(_sample_tasks())
    mgr.mark_complete(cp, "task-1", "plan-1", {"task_id": "task-1"})
    cp2 = mgr.load_or_create(_sample_tasks())
    assert cp2.tasks["task-1"].status == "complete"


def test_mark_complete_serializes_terminal_grounded_score(tmp_path):
    CheckpointManager.CHECKPOINT_DIR = tmp_path
    mgr = CheckpointManager("test")
    cp = mgr.load_or_create(_sample_tasks())
    mgr.mark_complete(
        cp,
        "task-1",
        "plan-1",
        {
            "task_id": "task-1",
            "terminal_score": TerminalGroundedScore(total_score=0.25, reason="checkpoint"),
        },
    )

    cp2 = mgr.load_or_create(_sample_tasks())
    assert cp2.tasks["task-1"].status == "complete"
    assert cp2.tasks["task-1"].result["terminal_score"]["reason"] == "checkpoint"


def test_save_is_atomic(tmp_path, monkeypatch):
    CheckpointManager.CHECKPOINT_DIR = tmp_path
    mgr = CheckpointManager("test")
    cp = RunCheckpoint(version=1, card_id="test", tasks={})
    calls = []

    original_replace = os.replace

    def _record(src, dst):
        calls.append((src, dst))
        return original_replace(src, dst)

    monkeypatch.setattr(os, "replace", _record)
    mgr.save(cp)
    assert len(calls) == 1
    assert str(calls[0][0]).endswith(".json.tmp")


def test_checkpoint_dir_permissions(tmp_path):
    CheckpointManager.CHECKPOINT_DIR = tmp_path
    mgr = CheckpointManager("test")
    mgr.load_or_create(_sample_tasks())
    stat = mgr.CHECKPOINT_DIR.stat()
    assert stat.st_mode & 0o777 == 0o700
