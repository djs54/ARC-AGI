# Plan A-034 — Restore offline setup documentation

## Card metadata

- **Card:** `backlog/A034.md`
- **Layer:** docs/process
- **Priority:** P2
- **Depends on:** A029, A030

## Summary

Three tests in `tests/test_arc_offline_bundle.py` fail because `docs/arc3-offline-setup.md` does not exist. The offline manifest legitimately references this doc as the user guide for the ARC3 offline submission bundle. The missing file blocks the full test suite from green.

The solution is to create a short, honest documentation file that describes:

1. What the bundle is (name, purpose, size range, components)
2. How to build it (script, flags, command line)
3. How to verify it (script, flags, command line)
4. Expected output layout (component structure)
5. Where to find setup instructions (refer to README)

All claims must be grounded in actual code behavior from:

- `benchmarks/arc3/package_offline_assets.py` — the bundle builder
- `benchmarks/arc3/verify_offline_bundle.py` — the bundle verifier
- `benchmarks/arc3/offline_manifest.json` — the manifest with component list and size constraints

## Implementation approach

### Content inventory from source code

**package_offline_assets.py:**

- Command-line entry point via `argparse` (lines 125–155)
- Flags: `--bundle-dir` (default: `sidequests-offline-submission`), `--manifest`, `--skip-clean`
- Workflow: read manifest, copy sources, compute sizes, generate manifest + checksums
- Output: bundle directory with `offline_manifest.json` and `checksums.txt`

**verify_offline_bundle.py:**

- Command-line entry point via `argparse` (lines 165–178)
- Flag: `--bundle-dir` (default: `sidequests-offline-submission`)
- Workflow: load manifest, verify checksums, validate components, check config/models/wheels
- Size constraint check: min >= 10 GB, max <= 50 GB (lines 151–154)

**offline_manifest.json:**

- Bundle name: `sidequests-offline-submission`
- Size range: 10–50 GB
- Components: code, docs (our file!), wheels, models, datasets, config, verify-script
- Checksum algorithm: sha256
- Checksum file: checksums.txt

### Doc structure (target: ~100–120 lines)

1. **Overview** — bundle purpose, name, contents list, size range
2. **Building the Bundle** — script, flags, what it does, example with custom options
3. **Verifying the Bundle** — script, flags, what it does, example with custom options
4. **Bundle Structure** — file layout (code/, wheels/, models/, datasets/, config.yaml, etc.)
5. **Environment Setup** — reference README (do not fabricate)
6. **Troubleshooting** — common issues and how to debug

### Fabrication risks to avoid

- Do NOT invent CI steps (not in the code)
- Do NOT describe cloud deployment (not in the code)
- Do NOT fabricate model download procedures (models come pre-cached in the bundle)
- Do NOT claim the script builds wheels (it only copies pre-built wheels)
- Do NOT specify Python version (not enforced in the scripts)
- Do NOT describe how to run the ARC3 evaluation (out of scope; refer to README)

### Required literal strings

The doc MUST contain:

- `package_offline_assets.py`
- `verify_offline_bundle.py`
- `sidequests-offline-submission`

## Concrete file edits

Create new file: `docs/arc3-offline-setup.md`

1. Read source files: `benchmarks/arc3/package_offline_assets.py`, `verify_offline_bundle.py`, `offline_manifest.json`
2. Extract command-line flags and behavior from each source
3. Write overview section grounded in offline_manifest.json
4. Document build workflow with exact command and flag names
5. Document verify workflow with exact command and flag names
6. Describe bundle structure (directory names) based on offline_manifest.json component `dest` values
7. Add environment setup pointer to README
8. Add troubleshooting section
9. Verify all three required strings appear in the body

## API / interface changes

None. This is documentation only.

## Tests to add or run

```bash
.venv/bin/python -m pytest tests/test_arc_offline_bundle.py -v
make test-a
```

Expected results:

- `test_build_sample_bundle` — PASS (doc file will exist and copy successfully)
- `test_verify_sample_bundle` — PASS (checksum verification will pass with doc present)
- `test_offline_doc_references_scripts` — PASS (doc will contain all three required strings)
- `test_manifest_size_range` — PASS (pre-existing, already green)
- `make test-a` — all 18/18 A-series tests still green

## Validation commands

```bash
# Check test results
.venv/bin/python -m pytest tests/test_arc_offline_bundle.py -v

# Verify A-series baseline stays green
make test-a

# Spot-check doc contains required strings
grep -E "package_offline_assets\.py|verify_offline_bundle\.py|sidequests-offline-submission" docs/arc3-offline-setup.md
```

## Assumptions / defaults

- The offline manifest's component list (`code`, `wheels`, `models`, `datasets`, `config`, `verify-script`) is the canonical spec for bundle structure
- `package_offline_assets.py` and `verify_offline_bundle.py` are the only tools needed to build and verify the bundle (no other scripts)
- The bundle is meant for offline ARC3 submission evaluation, not for production integration or local development
- Environment setup (Python, pip, venv) is documented in the repo README and should not be duplicated
- The offline assets directories (models, datasets) are pre-populated before bundling (not created by the packaging script)

## Addendum — `benchmarks/config.yaml`

After the doc landed, `test_build_sample_bundle` and `test_verify_sample_bundle` still failed on a *second* missing manifest source: `benchmarks/config.yaml`. The plan is extended to also create this file, with minimal content that satisfies the two assertions in `verify_offline_bundle._verify_config` (at `benchmarks/arc3/verify_offline_bundle.py:104-114`):

- `global.provider: ollama`
- top-level `benchmarks:` section present

Model identifiers in the `benchmarks.arc3` section mirror `benchmarks/arc3/model_budget.yaml::selected_models` to stay internally consistent. No runtime tuning knobs are invented beyond what the verifier checks.

