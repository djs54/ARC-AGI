"""A149: SingleTaskRunner._sync_tasks_with_live_catalog reconciles manifest task
game_ids against the live ARC /api/games catalog (the API rotates game ids, so a
static manifest goes stale and RESET would target a missing game)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from benchmarks.ab_harness import ABTask

import arc_runtime.runner_shell as runner_shell


def _runner(tasks, *, list_games_result=None, list_games_exc=None):
    runner = object.__new__(runner_shell.SingleTaskRunner)
    runner.real_api = True
    runner.tasks = tasks

    async def _list_games():
        if list_games_exc is not None:
            raise list_games_exc
        return list_games_result if list_games_result is not None else []

    runner.harness = SimpleNamespace(list_games=_list_games)
    return runner


def _sync(runner):
    asyncio.run(runner._sync_tasks_with_live_catalog())


def _task(task_id, game_id):
    t = ABTask(task_id=task_id, category="core", prompt="x")
    setattr(t, "game_id", game_id)
    return t


def test_generates_tasks_when_manifest_empty():
    runner = _runner(
        [],
        list_games_result=[
            {"game_id": "ls20-aaaa", "title": "LS20", "tags": ["keyboard"]},
            {"game_id": "tr87-bbbb", "title": "TR87", "tags": []},
        ],
    )
    _sync(runner)
    assert [t.game_id for t in runner.tasks] == ["ls20-aaaa", "tr87-bbbb"]
    assert runner.tasks[0].arc_game_title == "LS20"
    assert runner.tasks[0].arc_game_tags == ["keyboard"]


def test_remaps_stale_game_id():
    runner = _runner(
        [_task("t1", "stale-9999")],
        list_games_result=[{"game_id": "ls20-aaaa", "title": "LS20", "tags": ["keyboard"]}],
    )
    _sync(runner)
    # Stale id not in catalog → remapped to a live game id.
    assert runner.tasks[0].game_id == "ls20-aaaa"
    assert runner.tasks[0].arc_game_title == "LS20"


def test_keeps_valid_game_id_and_refreshes_metadata():
    runner = _runner(
        [_task("t1", "ls20-aaaa")],
        list_games_result=[{"game_id": "ls20-aaaa", "title": "LS20 v2", "tags": ["maze"]}],
    )
    _sync(runner)
    assert runner.tasks[0].game_id == "ls20-aaaa"  # unchanged
    assert runner.tasks[0].arc_game_title == "LS20 v2"  # refreshed
    assert runner.tasks[0].arc_game_tags == ["maze"]


def test_list_games_failure_falls_back_to_manifest():
    runner = _runner([_task("t1", "manifest-1")], list_games_exc=RuntimeError("api down"))
    _sync(runner)
    # No crash; manifest left as-is.
    assert runner.tasks[0].game_id == "manifest-1"


def test_empty_catalog_keeps_manifest():
    runner = _runner([_task("t1", "manifest-1")], list_games_result=[])
    _sync(runner)
    assert runner.tasks[0].game_id == "manifest-1"
