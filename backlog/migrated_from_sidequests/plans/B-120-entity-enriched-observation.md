# B-120 - Entity-Enriched Observation in Prompt Blocks

## Metadata

- Card: B120
- Priority: P0
- Dependencies: B119, B117

## Summary

Enrich observation summaries and prompt blocks with entity role context so the LLM can ground
action choices in what entities are doing. Currently `_summarize_puzzle_structure()` and
`_format_observation_section()` only report raw colors/shapes — the LLM sees "color 5 (12 cells)"
instead of "color 5 = PLAYER at row 3, col 7".

## Technical Approach

1. Update `_summarize_puzzle_structure()` to include entity roles alongside color descriptions.
2. Update `_format_observation_section()` to annotate color lines with known roles.
3. Add an ENTITY_CONTEXT block to `build_action_packet()` when entity map is populated.
4. Update `docs/arc-harness-rules.md` to document the new block.

All changes must fall back gracefully when `_entity_map` is empty.

## Concrete File Changes

### `agents/arc3/orchestrator.py`

**Update `_summarize_puzzle_structure()`:**

After the existing `color_desc` line, add entity annotations:

```python
# After color_desc computation:
if self._entity_map:
    entity_annotations = []
    for color_info in colors[:6]:
        cid = color_info["value"] if isinstance(color_info, dict) else color_info
        entity = self._entity_map.get(cid)
        if entity and entity["role"] != "unknown":
            annotation = f"color {cid} = {entity['role']}"
            if entity.get("position"):
                annotation += f" at row {entity['position']['row']:.0f}, col {entity['position']['col']:.0f}"
            entity_annotations.append(annotation)
    entity_desc = "; ".join(entity_annotations) if entity_annotations else "pending"
else:
    entity_desc = "pending"

# Append to the return string: f"Entity roles: {entity_desc}. "
```

**Update `_format_observation_section()`:**

Annotate color summary with roles:

```python
# Replace color_summary computation:
if colors:
    color_parts = []
    for c in colors[:6]:
        cid = c["value"]
        part = f"{cid}:{c['count']}"
        entity = self._entity_map.get(cid) if self._entity_map else None
        if entity and entity["role"] != "unknown":
            part += f"({entity['role']})"
        color_parts.append(part)
    color_summary = ", ".join(color_parts)
else:
    color_summary = "none"
```

**Update `build_action_packet()`:**

After the STATE block and before the MEMORY block, add:

```python
if self._entity_map:
    entity_lines = []
    for cid, info in self._entity_map.items():
        if info["role"] == "unknown":
            continue
        line = f"Color {cid}: {info['role']} (confidence={info['confidence']:.0%})"
        if info.get("position"):
            line += f" at row {info['position']['row']:.0f}, col {info['position']['col']:.0f}"
        entity_lines.append(line)
    if entity_lines:
        packet.blocks.append(ContentBlock(
            type="ENTITY_CONTEXT",
            content="\n".join(entity_lines),
            header="ENTITY CONTEXT",
        ))
```

Update `PromptPacket.render()` ordered_keys to include `"ENTITY_CONTEXT"` after `"STATE"`:

```python
ordered_keys = [
    "SYSTEM", "STATE", "ENTITY_CONTEXT", "MEMORY", "SOLVE_CONTEXT", "PLAN",
    ...
]
```

And add to headers dict:

```python
"ENTITY_CONTEXT": "ENTITY CONTEXT",
```

### `docs/arc-harness-rules.md`

**Under `## Prompt Block Ownership`, add:**

```markdown
7. EntityContextBlock
- producer: ARC Agent using SolveEngine ObjectRoleMapper
```

**Update Block-by-Phase Matrix:**

| Phase | ... | EntityContextBlock | ... |
|---|---|---|---|
| bootstrap | ... | O | ... |
| hypothesize | ... | O | ... |
| solve | ... | O | ... |
| act | ... | E | ... |
| ingest | ... | - | ... |
| evaluate | ... | - | ... |
| finalization | ... | - | ... |

**Add note:**
- `EntityContextBlock` appears when `_entity_map` has at least one non-UNKNOWN role.

### `tests/test_arc3_orchestrator.py`

Add tests:

```python
def test_observation_section_includes_entity_roles():
    """Color summary includes role annotations when entity_map populated."""

def test_observation_section_fallback_without_entity_map():
    """Color summary is plain when entity_map is empty."""

def test_puzzle_structure_includes_entity_roles():
    """Structure summary includes entity role annotations."""

def test_action_packet_includes_entity_context_block():
    """Prompt packet has ENTITY_CONTEXT block when entity_map populated."""

def test_action_packet_no_entity_context_when_empty():
    """Prompt packet omits ENTITY_CONTEXT when entity_map empty."""
```

## Validation Commands

```bash
pytest -q tests/test_arc3_orchestrator.py -k "entity"
pytest -q tests/test_arc3_orchestrator.py tests/test_arc3_durable_runner.py
```

## Risks / Constraints

- Do NOT break existing observation format. All entity annotations are additive.
- Fallback to current behavior when `_entity_map` is empty — no conditional branches that crash.
- Keep entity annotations compact (one line per entity). Do not bloat the prompt.
- `EntityContextBlock` uses the same `ContentBlock` type from B117.
- The block-by-phase matrix update is documentation-only.

## Outcome

The LLM sees entity-grounded context in every prompt. Instead of "color 5 (12 cells)", it sees
"color 5 = PLAYER at row 3, col 7". This enables strategic reasoning even when available_actions
is constrained.
