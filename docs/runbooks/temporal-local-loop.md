# ARC v2 Temporal Local Loop

This runbook documents the full sequence to run an ARC puzzle through the Temporal.io workflow orchestration engine in a local development environment.

## Prerequisites

- Docker running locally
- `pip install -e '.[temporal]'` (installs `temporalio>=1.7.0`)
- ARC_AGI repository cloned and development environment initialized

## Start Sequence

### Terminal 1: Start Temporal Infrastructure

```bash
cd /Users/djshelton/Desktop/GitProjects/ARC_AGI
make temporal-up
```

This starts the Docker Compose stack:
- `temporalio/auto-setup` (Temporal server + frontend)
- `postgres:16-alpine` (persistence backend)
- Temporal UI accessible at `http://localhost:8233`

Wait ~5-10 seconds for startup to complete.

### Terminal 2: Start Temporal Worker

```bash
cd /Users/djshelton/Desktop/GitProjects/ARC_AGI
source .venv/bin/activate
ARC_TEMPORAL_ENABLED=1 python -m agents.arc4.temporal_worker
```

Expected output:
```
INFO - Starting Temporal worker...
INFO - Worker started, polling task queue: arc-agent
```

The worker will poll `arc-agent` task queue for workflow executions and activity tasks.

### Terminal 3: Run a Puzzle Through Temporal

```bash
cd /Users/djshelton/Desktop/GitProjects/ARC_AGI
source .venv/bin/activate
ARC_TEMPORAL_ENABLED=1 python run_single_puzzle.py --agent-version=v2 --task-id=demo --temporal
```

Or using the CLI flag instead of env var:

```bash
python run_single_puzzle.py --agent-version=v2 --task-id=demo --temporal
```

Expected flow:
1. `run_single_puzzle.py` checks `--temporal` flag
2. Dispatches workflow to Temporal at `localhost:7233`
3. Worker receives workflow execution task
4. Worker executes perceive → resolve → plan → vet → execute → evaluate phases
5. Each phase runs as a Temporal activity with retry/timeout handling
6. Result is deserialized and telemetry artifacts are written

## Inspect Results

### Temporal UI

Open `http://localhost:8233` in your browser:
- **Workflows**: View workflow history and status
- **Per-Activity Timing**: See execution time for each phase activity
- **Task Queue**: Monitor `arc-agent` queue depth
- **Logs**: View activity-level logs and errors

### Run Result Artifacts

```bash
# View final result summary
cat results/demo/agent_execution_trace.json | jq '.status, .completed_cycles'

# View telemetry
cat agent_execution_trace.json | jq '.[] | select(.operation=="evaluate")'
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ARC_TEMPORAL_ENABLED` | `0` | Enable Temporal dispatch (`1` = enabled, `0` = inline fallback) |
| `ARC_TEMPORAL_TARGET` | `localhost:7233` | Temporal frontend address |
| `ARC_TEMPORAL_NAMESPACE` | `default` | Temporal namespace |
| `ARC_TEMPORAL_TASK_QUEUE` | `arc-agent` | Worker task queue name |

Set these before running worker and client if using non-default values:

```bash
export ARC_TEMPORAL_TARGET=temporal.example.com:7233
export ARC_TEMPORAL_NAMESPACE=production
export ARC_TEMPORAL_TASK_QUEUE=arc-solver
python -m agents.arc4.temporal_worker
```

## Troubleshooting

### Connection Refused on :7233

**Problem**: `ConnectionRefusedError: connection refused` from temporal client

**Solution**:
1. Verify Docker is running: `docker ps | grep temporal`
2. Run `make temporal-up` in Terminal 1
3. Wait 10+ seconds for Temporal to start
4. Check logs: `docker compose -f docker-compose.temporal.yml logs`

### Worker Not Polling (Worker Starts But Idle)

**Problem**: Worker shows "polling task queue: arc-agent" but receives no tasks

**Solution**:
1. Verify task queue name matches across worker and client
2. Check worker logs for connection errors
3. Restart worker: `Ctrl+C` then rerun with `ARC_TEMPORAL_ENABLED=1 python -m agents.arc4.temporal_worker`
4. Check Temporal UI at http://localhost:8233 → **Task Queues** tab

### Activity Timeout (Timeout After 300 Seconds)

**Problem**: Activity completes but client reports `ActivityTimeoutError`

**Solution**:
1. Default timeout is 300 seconds per activity phase
2. For slower LLMs (e.g., local Ollama), increase timeout in `agents/arc4/temporal_workflows.py`:
   ```python
   ACTIVITY_TIMEOUT = timedelta(seconds=600)  # 10 minutes
   ```
3. Restart worker with new timeout
4. Re-run puzzle

### Fallback to Inline Execution (Expected Behavior)

**Scenario**: `ARC_TEMPORAL_ENABLED=1` but puzzle still runs inline

**Expected causes**:
- Temporal server is down → connection refused, graceful fallback
- Worker not running or not polling correct queue → no task reception, client times out, fallback
- `temporalio` package not installed → `HAS_TEMPORAL=False`, inline only

**Inspect logs**:
```bash
# Client logs
python run_single_puzzle.py ... 2>&1 | grep -i temporal

# Worker logs
grep -i "error\|exception" temporal_worker.log
```

## Teardown

```bash
# Terminal 1 or new terminal:
make temporal-down
```

This stops and removes the Docker Compose stack (temporary volumes are cleaned up).

## Full Example Smoke Test (Automated)

Use the `make smoke-temporal` target to run a complete local-loop test:

```bash
cd /Users/djshelton/Desktop/GitProjects/ARC_AGI
make smoke-temporal
```

This target:
1. Starts Temporal (`make temporal-up`)
2. Starts the worker in background
3. Runs one puzzle via `--temporal`
4. Stops the worker
5. Stops Temporal (`make temporal-down`)

Exit code 0 = success.

## References

- [Temporal.io Python SDK Docs](https://temporal.io/docs/python)
- [Workflow Definition Pattern](https://temporal.io/docs/python/workflows)
- [Activity Timeout & Retry](https://temporal.io/docs/python/activities)
- ARC_AGI Architecture: [ARCHITECTURE.md](../../ARCHITECTURE.md)
