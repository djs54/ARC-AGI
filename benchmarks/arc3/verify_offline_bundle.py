"""Verify the sidequests ARC3 offline submission bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Dict, Iterable

import yaml

CHECKSUM_FILENAME = "checksums.txt"
MANIFEST_FILENAME = "offline_manifest.json"
REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_BUDGET_PATH = REPO_ROOT / "benchmarks/arc3/model_budget.yaml"


def _resolve_dest(bundle_dir: Path, component: dict) -> Path:

    dest = component.get("dest") or component["name"]
    candidate = Path(dest)
    if candidate in (Path("."), Path("")):
        return bundle_dir
    return bundle_dir / candidate


def _load_manifest(bundle_dir: Path) -> dict:
    manifest_path = bundle_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing manifest at {manifest_path}")
    return json.loads(manifest_path.read_text())


def _parse_checksums(checksum_path: Path) -> Dict[Path, str]:
    if not checksum_path.exists():
        raise FileNotFoundError(f"Missing checksum file at {checksum_path}")
    entries: Dict[Path, str] = {}
    with checksum_path.open() as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                raise ValueError(f"Malformed checksum line: {line}")
            digest, rel_path = parts
            entries[Path(rel_path)] = digest
    return entries


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(32_768), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _verify_checksums(bundle_dir: Path, entries: Dict[Path, str]) -> None:
    actual_files = {
        path.relative_to(bundle_dir)
        for path in bundle_dir.rglob("*")
        if path.is_file() and path.name != CHECKSUM_FILENAME
    }
    expected_files = set(entries.keys())
    missing = actual_files - expected_files
    extra = expected_files - actual_files
    if missing or extra:
        missing_part = ", ".join(sorted(str(p) for p in missing))
        extra_part = ", ".join(sorted(str(p) for p in extra))
        raise AssertionError(
            f"Checksum mismatch. Missing: {missing_part}; Extra: {extra_part}"
        )
    for rel_path, digest in entries.items():
        target = bundle_dir / rel_path
        if not target.exists():
            raise FileNotFoundError(f"File listed in checksum missing: {rel_path}")
        actual = _sha256(target)
        if actual != digest:
            raise AssertionError(
                f"Checksum failure for {rel_path}: {actual} != {digest}"
            )


def _verify_components(bundle_dir: Path, manifest: dict) -> None:
    for component in manifest.get("components", []):
        dest_root = _resolve_dest(bundle_dir, component)
        for source in component.get("sources", []):
            source_name = Path(source).name
            candidate = dest_root / source_name
            if candidate.exists():
                destination = candidate
            elif dest_root.name == source_name and dest_root.exists():
                destination = dest_root
            else:
                raise FileNotFoundError(
                    f"Component {component['name']} missing {candidate}"
                )
            if destination.is_dir() and not any(destination.iterdir()):
                raise AssertionError(f"Directory {destination} is empty")


def _verify_config(bundle_dir: Path) -> None:
    config_path = bundle_dir / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError("Missing config.yaml in bundle root")
    config = yaml.safe_load(config_path.read_text())
    global_conf = config.get("global") or {}
    provider = global_conf.get("provider")
    if provider != "ollama":
        raise AssertionError("Config provider must be 'ollama' for offline runs")
    if "benchmarks" not in config:
        raise AssertionError("Config is missing a benchmarks section")


def _verify_models(bundle_dir: Path) -> None:
    manifest_path = bundle_dir / "models" / "models_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("models/models_manifest.json is missing")
    models_manifest = json.loads(manifest_path.read_text())
    entries = {f"{entry['name']}:{entry['tag']}" for entry in models_manifest.get("models", [])}
    budget = yaml.safe_load(MODEL_BUDGET_PATH.read_text())
    expected_models = set(
        budget.get("integration", {})
        .get("offline_bundle", {})
        .get("models_to_package", [])
    )
    missing = expected_models - entries
    if missing:
        raise AssertionError(
            f"Models manifest misses expected models: {', '.join(sorted(missing))}"
        )


def _verify_wheels(bundle_dir: Path) -> None:
    wheels_dir = bundle_dir / "wheels"
    if not wheels_dir.exists():
        raise FileNotFoundError("wheels directory is missing")
    wheel_files = list(wheels_dir.rglob("*.whl"))
    if not wheel_files:
        raise AssertionError("No wheel files found in wheels/ directory")


def verify_offline_bundle(bundle_dir: Path) -> None:
    manifest = _load_manifest(bundle_dir)
    bundle_info = manifest.get("bundle", {})
    size_range = bundle_info.get("expected_size_gb", {})
    min_size = size_range.get("min")
    max_size = size_range.get("max")
    if min_size is None or min_size < 10:
        raise AssertionError("Expected bundle min size must be at least 10 GB")
    if max_size is None or max_size > 50:
        raise AssertionError("Expected bundle max size must not exceed 50 GB")
    checksum_file = bundle_dir / bundle_info.get("checksum_file", CHECKSUM_FILENAME)
    entries = _parse_checksums(checksum_file)
    _verify_components(bundle_dir, manifest)
    _verify_checksums(bundle_dir, entries)
    _verify_config(bundle_dir)
    _verify_models(bundle_dir)
    _verify_wheels(bundle_dir)
    print("Offline bundle verified: checksums, config, models, wheels are present.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify an ARC3 offline bundle.")
    parser.add_argument(
        "--bundle-dir",
        type=Path,
        default=Path("sidequests-offline-submission"),
        help="Directory containing the offline bundle",
    )
    args = parser.parse_args()
    verify_offline_bundle(args.bundle_dir)


if __name__ == "__main__":
    main()
