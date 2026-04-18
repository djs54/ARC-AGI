# A-016 - Enable Phoenix Observability By Default During Smoke Runs

## Card metadata

- Card: A016
- Priority: P0
- Layer: transport/client seam
- Depends on: A014

## Summary

Wire the smoke entrypoint so that Phoenix/OTEL observability is active by default whenever the required Python packages are present. Without this change, the shim added by A014 stays inert across every smoke run because the user's `~/.sidequests/config.toml` has no `[observability]` block and the smoke entrypoint never sets `PHOENIX_ENABLE`.

## Implementation approach

1. **Preflight auto-enable**.

   In `run_single_puzzle.py`, modify `_enforce_observability_preflight` so that it performs the following at the top of the function:

   - Probe dependencies and auto-enable if available and not explicitly disabled.
   - Set `os.environ["PHOENIX_ENABLE"] = "1"`.
   - Mutate `config["observability"]["enabled"] = True`.

2. **Project name documentation**.

   Update `ARCHITECTURE.md` with observability defaults.

3. **Update docstring**.

   Update the `Observability` class docstring in `sidequest_mcp_client/observability.py`.

4. **Tests**.

   Add tests to `tests/test_observability.py` for auto-enable and explicit disable.
