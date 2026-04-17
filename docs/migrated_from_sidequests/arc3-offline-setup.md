# ARC3 Offline Submission Bundle

This document describes how to build, verify, and consume the **`sidequests-offline-submission/`** archive that ships SideQuests + ARC-AGI-3 assets into constrained, network-disabled evaluation environments.

## Bundle layout (B59 acceptance criteria)

```
sidequests-offline-submission/
├── code/                        # SideQuests runtime + ARC adapter
├── wheels/                      # Python wheels (kuzu, sentence-transformers, etc.)
├── models/                      # Downloaded Ollama weights for primary + fallback
├── datasets/                    # Cached ARC calibration puzzles
├── config.yaml                 # Runtime configuration driven by benchmarks/config.yaml
├── verify_offline_bundle.py     # Offline verification helper
└── checksums.txt               # SHA256 for every artifact inside the bundle
```

The manifest at `benchmarks/arc3/offline_manifest.json` defines the sources, destinations, and the expected bundle size range (10–50 GB) that keeps the submission compliant with ARC-AGI-3 contest rules.

## Building the offline bundle

1. Resolve every source asset locally:
   - `sidequests` + `benchmarks/arc3` source trees (already in this repo).
   - Python wheels such as `dist/sidequests_brain-0.1.0rc1-py3-none-any.whl`.
   - Ollama models (pull `llama3.1:8b-instruct-q5` and `llama2:7b-q4`).
   - Cached puzzles under `benchmarks/arc3/offline_assets/datasets`.
2. Run the packaging script from the repository root (requires Python + local assets):
   ```sh
   python benchmarks/arc3/package_offline_assets.py --bundle-dir /path/to/sidequests-offline-submission
   ```
   The script copies every manifest entry, computes SHA256 checksums, and writes the updated manifest/backing files inside the bundle.
3. Confirm `sidequests-offline-submission/offline_manifest.json` and `checksums.txt` exist and reference the same files you copied.

## Verifying the offline bundle (network disabled)

Before submitting or handing the bundle to ARC judges, run the verifier on a host with no internet access:

```sh
cd /path/to/sidequests-offline-submission
python benchmarks/arc3/verify_offline_bundle.py --bundle-dir .
```

The verifier performs the following offline checks:
- Ensures every manifest component (`code`, `wheels`, `models`, `datasets`, `config`) is present and non-empty.
- Validates every file hash against `checksums.txt` using SHA256.
- Loads `config.yaml` to ensure the `global.provider` is `ollama` and at least one ARC benchmark configuration exists.
- Reads `models/models_manifest.json` to confirm both `llama3.1:8b-instruct-q5` and `llama2:7b-q4` are candidates that can be loaded without network.

## Acceptance checklist

- [x] Automated packaging script that reproduces the bundle from source + downloaded assets.
- [x] Offline verification guards (`verify_offline_bundle.py`) pass without reaching the network.
- [x] Environment configuration (`config.yaml`) is shipped in the bundle and parsed by the verifier.
- [x] `checksums.txt` covers every file added to the bundle.
- [x] Bundle size metadata stays between 10 GB and 50 GB (as recorded in the manifest).
- [x] Documentation (this file) covers build, verify, and offline reuse workflows.
