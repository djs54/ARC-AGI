# B-59-arc-offline-bundle — Offline Packaging + Reproducible Execution Bundle

**Card:** B59 | **Priority:** P12 | **Depends on:** B54, B58 (rules + model strategy)

## Summary
Build reproducible offline bundle for submission. Packages code, wheels, models, and config into portable archive for constrained evaluation environment.

## Technical Approach

### Bundle Contents
```
sidequests-offline-submission/
├── code/                   # SideQuests source + ARC adapter
├── wheels/                 # Python packages (kuzu, sentence-transformers, etc.)
├── models/                 # Downloaded model weights
├── datasets/               # Cached benchmark puzzles
├── config.yaml             # Runtime configuration
├── verify_offline_bundle.py # Validation script
└── checksums.txt          # SHA256 hashes for all artifacts
```

### Packaging Process
1. `benchmarks/arc3/package_offline_assets.py` — script to build bundle
2. Download models, wheels, dataset once
3. Create checksums for all artifacts
4. Generate manifest with versions and sizes

### Verification
- `verify_offline_bundle.py` runs completely offline (network disabled)
- Checksums validate all artifacts
- Model load test: can load models without internet
- Environment setup test: all dependencies are present

## Files to Create/Modify

- `benchmarks/arc3/package_offline_assets.py` — bundling script
- `benchmarks/arc3/offline_manifest.json` — manifest schema
- `benchmarks/arc3/verify_offline_bundle.py` — validation script
- `docs/arc3-offline-setup.md` — user guide for bundle usage

## Acceptance Criteria

1. Bundle creation script is automated and reproducible
2. Bundle verification passes with network disabled
3. Environment setup from bundle succeeds
4. Checksums validate for all required assets
5. Bundle size is reasonable (>10GB, <50GB)
6. Documentation is clear for offline evaluation

## Notes

- Critical for contest compliance and reproducibility
- Bundle must be hermetic (no runtime downloads)
