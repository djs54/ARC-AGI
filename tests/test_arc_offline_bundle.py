"""Tests for ARC3 offline bundle automation."""

import json
from pathlib import Path

from benchmarks.arc3.package_offline_assets import build_offline_bundle
from benchmarks.arc3.verify_offline_bundle import verify_offline_bundle


REPO_ROOT = Path(__file__).resolve().parents[1]


def _create_sample_manifest(tmp_path: Path) -> Path:
    sample_code = tmp_path / "sample_code"
    sample_code.mkdir()
    (sample_code / "run.py").write_text("print('offline bundle sample')\n")

    wheel = tmp_path / "sample_package.whl"
    wheel.write_bytes(b"wheel-placeholder")

    manifest = {
        "bundle": {
            "name": "sidequests-offline-submission",
            "version": "0.1.test",
            "description": "Sample manifest for testing.",
            "expected_size_gb": {"min": 10, "max": 50},
            "checksum_algorithm": "sha256",
            "checksum_file": "checksums.txt",
        },
        "components": [
            {
                "name": "code",
                "dest": "code",
                "sources": [str(sample_code.resolve())],
            },
            {
                "name": "docs",
                "dest": "code/docs",
                "sources": [str((REPO_ROOT / "docs" / "arc3-offline-setup.md").resolve())],
            },
            {
                "name": "wheels",
                "dest": "wheels",
                "sources": [str(wheel.resolve())],
            },
            {
                "name": "models",
                "dest": "models",
                "sources": [
                    str((REPO_ROOT / "benchmarks" / "arc3" / "offline_assets" / "models").resolve())
                ],
            },
            {
                "name": "datasets",
                "dest": "datasets",
                "sources": [
                    str((REPO_ROOT / "benchmarks" / "arc3" / "offline_assets" / "datasets").resolve())
                ],
            },
            {
                "name": "config",
                "dest": ".",
                "sources": [str((REPO_ROOT / "benchmarks" / "config.yaml").resolve())],
            },
            {
                "name": "verify-script",
                "dest": ".",
                "sources": [
                    str((REPO_ROOT / "benchmarks" / "arc3" / "verify_offline_bundle.py").resolve())
                ],
            },
        ],
    }

    manifest_path = tmp_path / "sample_manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    return manifest_path


def test_build_sample_bundle(tmp_path: Path) -> None:
    manifest_path = _create_sample_manifest(tmp_path)
    bundle_dir = tmp_path / "bundle"
    output_manifest, checksum_count = build_offline_bundle(bundle_dir, manifest_path)

    assert (bundle_dir / "code" / "sample_code" / "run.py").exists()
    assert (bundle_dir / "code" / "docs" / "arc3-offline-setup.md").exists()
    assert (bundle_dir / "wheels" / "sample_package.whl").exists()
    assert (bundle_dir / "models" / "models_manifest.json").exists()
    assert (bundle_dir / "datasets" / "arc3_calibration.json").exists()
    assert (bundle_dir / "config.yaml").exists()
    assert (bundle_dir / "verify_offline_bundle.py").exists()
    assert checksum_count > 0
    assert output_manifest["bundle"]["component_count"] == len(output_manifest.get("components", []))


def test_verify_sample_bundle(tmp_path: Path) -> None:
    manifest_path = _create_sample_manifest(tmp_path)
    bundle_dir = tmp_path / "bundle"
    build_offline_bundle(bundle_dir, manifest_path)
    verify_offline_bundle(bundle_dir)


def test_manifest_size_range() -> None:
    manifest = json.loads(Path("benchmarks/arc3/offline_manifest.json").read_text())
    expected = manifest.get("bundle", {}).get("expected_size_gb", {})
    assert expected.get("min") >= 10
    assert expected.get("max") <= 50


def test_offline_doc_references_scripts() -> None:
    doc = Path("docs/arc3-offline-setup.md").read_text()
    assert "package_offline_assets.py" in doc
    assert "verify_offline_bundle.py" in doc
    assert "sidequests-offline-submission" in doc
