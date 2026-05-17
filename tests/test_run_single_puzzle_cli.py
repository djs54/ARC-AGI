import os
from pathlib import Path

import run_single_puzzle as rsp


def test_apply_llm_overrides_updates_only_llm_block():
    config = {
        "llm": {
            "provider": "ollama",
            "model": "llama3.1:8b",
            "base_url": "http://localhost:11434/v1",
        },
        "benchmark": {"max_attempts_per_puzzle": 15},
    }

    updated = rsp._apply_llm_overrides(
        config,
        {
            "model": "qwen2.5:7b",
            "timeout_seconds": 300,
            "max_retries": 5,
        },
    )

    assert updated["llm"]["model"] == "qwen2.5:7b"
    assert updated["llm"]["timeout_seconds"] == 300
    assert updated["llm"]["max_retries"] == 5
    assert updated["benchmark"] == {"max_attempts_per_puzzle": 15}


def test_remove_db_artifacts_deletes_wal_and_shm(tmp_path: Path):
    db_path = tmp_path / "brain.db"
    wal_path = Path(f"{db_path}.wal")
    shm_path = Path(f"{db_path}.shm")

    db_path.write_text("db")
    wal_path.write_text("wal")
    shm_path.write_text("shm")

    rsp._remove_db_artifacts(db_path)

    assert not db_path.exists()
    assert not wal_path.exists()
    assert not shm_path.exists()


def test_ensure_arc_api_key_loads_from_arc_json(tmp_path: Path, monkeypatch):
    arc_json = tmp_path / "arc.json"
    arc_json.write_text('{"key": " secret-key "}')
    monkeypatch.delenv("ARC_API_KEY", raising=False)

    loaded = rsp._ensure_arc_api_key(arc_json)

    assert loaded == "secret-key"
    assert os.environ["ARC_API_KEY"] == "secret-key"


def test_single_task_runner_prefers_campy_config(tmp_path: Path, monkeypatch):
    campy_config = tmp_path / "campy.toml"
    legacy_config = tmp_path / "sidequests.toml"
    campy_config.write_text("[llm]\nprovider = 'ollama'\n")
    legacy_config.write_text("[llm]\nprovider = 'legacy'\n")
    loaded_paths = []

    def fake_load_config(path):
        loaded_paths.append(Path(path))
        return {"llm": {"provider": "ollama"}}

    monkeypatch.setattr(rsp, "CONFIG_PATH", campy_config)
    monkeypatch.setattr(rsp, "LEGACY_CONFIG_PATH", legacy_config)
    monkeypatch.setattr(rsp, "load_config", fake_load_config)
    monkeypatch.setattr(rsp, "_enforce_llm_preflight", lambda config: None)
    monkeypatch.setattr(rsp, "_enforce_observability_preflight", lambda config: None)

    rsp.SingleTaskRunner()

    assert loaded_paths == [campy_config]


def test_single_task_runner_uses_legacy_config_fallback(tmp_path: Path, monkeypatch):
    campy_config = tmp_path / "campy.toml"
    legacy_config = tmp_path / "sidequests.toml"
    legacy_config.write_text("[llm]\nprovider = 'ollama'\n")
    loaded_paths = []

    def fake_load_config(path):
        loaded_paths.append(Path(path))
        return {"llm": {"provider": "ollama"}}

    monkeypatch.setattr(rsp, "CONFIG_PATH", campy_config)
    monkeypatch.setattr(rsp, "LEGACY_CONFIG_PATH", legacy_config)
    monkeypatch.setattr(rsp, "load_config", fake_load_config)
    monkeypatch.setattr(rsp, "_enforce_llm_preflight", lambda config: None)
    monkeypatch.setattr(rsp, "_enforce_observability_preflight", lambda config: None)

    rsp.SingleTaskRunner()

    assert loaded_paths == [legacy_config]
