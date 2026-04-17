"""Create a reproducible offline bundle for ARC3 submission."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "benchmarks/arc3/offline_manifest.json"
CHECKSUM_FILENAME = "checksums.txt"
MANIFEST_FILENAME = "offline_manifest.json"


def _resolve_dest(bundle_dir: Path, component: dict) -> Path:
    dest = component.get("dest") or component["name"]
    dest_path = Path(dest)
    if dest_path in (Path("."), Path("")):
        return bundle_dir
    return bundle_dir / dest_path


def _copy_source(source_root: Path, target_root: Path) -> List[Path]:
    copied: List[Path] = []
    target_root.mkdir(parents=True, exist_ok=True)
    if source_root.is_dir():
        if target_root.name == source_root.name:
            for child in source_root.iterdir():
                child_target = target_root / child.name
                if child.is_dir():
                    shutil.copytree(child, child_target, dirs_exist_ok=True)
                else:
                    child_target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(child, child_target)
            copied.append(target_root)
        else:
            target = target_root / source_root.name
            shutil.copytree(source_root, target, dirs_exist_ok=True)
            copied.append(target)
    else:
        target = target_root / source_root.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root, target)
        copied.append(target)
    return copied


def _sum_sizes(paths: Iterable[Path]) -> int:
    total = 0
    for root in paths:
        if root.is_file():
            total += root.stat().st_size
        else:
            for child in root.rglob("*"):
                if child.is_file():
                    total += child.stat().st_size
    return total


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(32_768), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _collect_file_paths(bundle_dir: Path) -> List[Path]:
    files = [path for path in bundle_dir.rglob("*") if path.is_file() and path.name != CHECKSUM_FILENAME]
    return sorted(files)


def build_offline_bundle(
    bundle_dir: Path,
    manifest_path: Path = DEFAULT_MANIFEST,
    skip_clean: bool = False,
) -> tuple[dict, int]:
    manifest = json.loads(manifest_path.read_text())
    if bundle_dir.exists():
        if skip_clean:
            bundle_dir.mkdir(parents=True, exist_ok=True)
        else:
            shutil.rmtree(bundle_dir)
            bundle_dir.mkdir(parents=True)
    else:
        bundle_dir.mkdir(parents=True)

    component_sizes: List[int] = []
    for component in manifest.get("components", []):
        dest_root = _resolve_dest(bundle_dir, component)
        copied_paths: List[Path] = []
        for source in component.get("sources", []):
            source_path = (REPO_ROOT / Path(source)).resolve()
            if not source_path.exists():
                raise FileNotFoundError(f"Manifest source missing: {source_path}")
            copied_paths.extend(_copy_source(source_path, dest_root))
        component_sizes.append(_sum_sizes(copied_paths))

    built_manifest = json.loads(json.dumps(manifest))
    bundle_meta = built_manifest.setdefault("bundle", {})
    bundle_meta["built_at"] = datetime.now(timezone.utc).isoformat()
    bundle_meta["component_count"] = len(built_manifest.get("components", []))
    bundle_meta["total_copied_size_bytes"] = sum(component_sizes)
    for record, size in zip(built_manifest.get("components", []), component_sizes):
        record["copied_size_bytes"] = size

    manifest_target = bundle_dir / MANIFEST_FILENAME
    manifest_target.write_text(json.dumps(built_manifest, indent=2, sort_keys=False))

    checksum_lines: List[str] = []
    for path in _collect_file_paths(bundle_dir):
        rel = path.relative_to(bundle_dir)
        digest = _sha256(path)
        checksum_lines.append(f"{digest}  {rel.as_posix()}")

    (bundle_dir / CHECKSUM_FILENAME).write_text("\n".join(checksum_lines))

    return built_manifest, len(checksum_lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Package ARC3 offline submission assets.")
    parser.add_argument(
        "--bundle-dir",
        type=Path,
        default=Path("sidequests-offline-submission"),
        help="Target directory for the offline bundle",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Source manifest that lists bundle components",
    )
    parser.add_argument(
        "--skip-clean",
        action="store_true",
        help="Do not delete the bundle directory before copying (for incremental inspection)",
    )

    args = parser.parse_args()
    manifest, checksum_count = build_offline_bundle(
        args.bundle_dir, args.manifest, skip_clean=args.skip_clean
    )
    checksum_path = args.bundle_dir / CHECKSUM_FILENAME
    print(f"Built offline bundle at {args.bundle_dir}")
    print(f"Manifest written to {args.bundle_dir / MANIFEST_FILENAME}")
    print(f"Checksum file with {checksum_count} entries written to {checksum_path}")


if __name__ == "__main__":
    main()
