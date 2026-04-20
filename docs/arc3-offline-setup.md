# ARC3 Offline Bundle Setup

## Overview

The sidequests-offline-submission bundle is a self-contained package designed for offline ARC3 submission. It contains:

- **Code**: SideQuests runtime, ARC adapter, and evaluation harness
- **Wheels**: Pinned Python dependencies for offline installation
- **Models**: Locally cached Ollama model directories
- **Datasets**: Cached ARC3 puzzles for calibration and verification
- **Configuration**: Runtime settings for offline evaluation (Ollama provider)
- **Verification script**: Helper for checking bundle integrity

The bundle is sized between 10-50 GB, enforced by the offline manifest.

## Building the Bundle

To build the offline bundle, use the `package_offline_assets.py` script:

```bash
python -m benchmarks.arc3.package_offline_assets
```

This command will:

1. Read the manifest from `benchmarks/arc3/offline_manifest.json`
2. Copy all listed sources (code, wheels, models, datasets, config) to the bundle directory
3. Generate a manifest file (`offline_manifest.json`) with component metadata
4. Compute SHA256 checksums for all files and write to `checksums.txt`

### Build Options

The script supports the following flags:

- `--bundle-dir PATH` — Target directory for the offline bundle (default: `sidequests-offline-submission`)
- `--manifest PATH` — Source manifest file (default: `benchmarks/arc3/offline_manifest.json`)
- `--skip-clean` — Do not delete the bundle directory before copying (useful for incremental inspection)

Example with custom output directory:

```bash
python -m benchmarks.arc3.package_offline_assets --bundle-dir /path/to/custom/bundle
```

## Verifying the Bundle

To verify the bundle after building, use the `verify_offline_bundle.py` script:

```bash
python -m benchmarks.arc3.verify_offline_bundle
```

This command will:

1. Load and validate the embedded manifest
2. Verify all file checksums against `checksums.txt`
3. Check that all required components are present (code, wheels, models, datasets, config)
4. Validate that the bundle's expected size is between 10–50 GB
5. Verify that offline configuration targets the Ollama provider

### Verification Options

- `--bundle-dir PATH` — Bundle directory to verify (default: `sidequests-offline-submission`)

Example with custom bundle directory:

```bash
python -m benchmarks.arc3.verify_offline_bundle --bundle-dir /path/to/bundle
```

## Bundle Structure

After building, the bundle directory will contain:

- `code/` — Runtime code and ARC adapter
- `wheels/` — Pinned Python package wheels
- `models/` — Cached Ollama model directories
- `datasets/` — Cached ARC3 puzzle datasets
- `config.yaml` — Runtime configuration (Ollama provider)
- `verify_offline_bundle.py` — Verification script
- `offline_manifest.json` — Bundle metadata (generated during build)
- `checksums.txt` — SHA256 checksums of all files (generated during build)

## Environment Setup

For local development and building, refer to the repo README for:

- Python version requirements
- Pip and virtual environment setup
- Installation of the SideQuests brain MCP

The offline bundle itself is self-contained and does not require these setup steps on the target evaluation system.

## Troubleshooting

If verification fails:

1. Check that all source directories listed in the manifest exist before building
2. Verify that `benchmarks/config.yaml` exists and contains the Ollama provider configuration
3. Ensure the bundle's total size falls within the 10–50 GB range
4. Re-run the verification script with the bundle directory specified

For detailed verification output, examine the `offline_manifest.json` file in the bundle root to confirm all components were packaged.
