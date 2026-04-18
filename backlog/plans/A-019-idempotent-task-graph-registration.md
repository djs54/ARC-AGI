# A-019 Idempotent Task Graph Registration Plan

## Implementation
1. **Sidequests MCP**: Modify the Kuzu `register_task_graph` query to use `MERGE` instead of `CREATE` for `TaskNode` and `DEPENDS_ON`.
2. **Client-side**: Wrap `register_task_graph` in `mcp_brain_client.py` to catch "violates the uniqueness constraint of the primary key column" and return an `ignored_duplicate` status.
