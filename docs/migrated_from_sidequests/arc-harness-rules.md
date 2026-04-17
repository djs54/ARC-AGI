# ARC Harness Rules

Status: active draft

Purpose: define ownership and guardrails so orchestration is unambiguous across ARC Harness, ARC Agent, SideQuests, and ARC API.

Companion references:
- Ecosystem rules (layer boundaries): [docs/ecosystem-rules.md](ecosystem-rules.md)
- Tool catalog: [docs/tool-catalog.md](tool-catalog.md)
- Architecture: [docs/ARCHITECTURE.md](ARCHITECTURE.md)

## Runtime Surfaces

1. ARC Harness
- Owns run lifecycle and orchestration.
- Owns phase transitions.
- Owns retry, checkpoint, and run export.

2. ARC Agent
- Owns reasoning and decision support.
- Builds prompt blocks and proposes actions.
- Does not own orchestration state machine.

3. SideQuests
- Owns memory and planning tools.
- Provides retrieval, plan registration, and outcome learning.
- Does not own ARC phase semantics.

4. ARC API (Environment)
- Owns puzzle state transitions and available_actions.
- Is the authority for WIN/GAME_OVER/NOT_FINISHED state.
- Does not own reasoning or orchestration.

## Orchestration Rule

ARC Harness is the single orchestration owner.

This means only ARC Harness should decide and stamp the active phase:
- bootstrap
- hypothesize
- solve
- act
- ingest
- evaluate
- finalization

ARC Agent and SideQuests may produce events within a phase, but they do not redefine phase semantics.

## Tool Contract Rules (SideQuests Tools)

Each tool has bounded allowed phases and mode.

Authoritative tool definitions and schemas live in [docs/tool-catalog.md](docs/tool-catalog.md). Use this file for ARC harness ownership constraints layered on top of those contracts.

1. notify_turn
- owner: SideQuests
- mode: write
- allowed phases: bootstrap, act, ingest, evaluate, finalization

2. current_truth
- owner: SideQuests
- mode: read
- allowed phases: bootstrap, ingest, act

3. register_plan
- owner: SideQuests
- mode: write
- allowed phases: bootstrap, solve

4. report_outcome
- owner: SideQuests
- mode: write
- allowed phases: evaluate, finalization

5. recall_plans
- owner: SideQuests
- mode: read
- allowed phases: bootstrap, solve

6. recall_lessons
- owner: SideQuests
- mode: read
- allowed phases: bootstrap, solve

7. analogical_search
- owner: SideQuests
- mode: read
- allowed phases: bootstrap, solve

8. branch_quest
- owner: SideQuests
- mode: write
- allowed phases: bootstrap

9. register_task_graph
- owner: SideQuests
- mode: write
- allowed phases: bootstrap, solve

10. get_ready_tasks
- owner: SideQuests
- mode: read
- allowed phases: solve

11. advance_task
- owner: SideQuests
- mode: write
- allowed phases: solve

12. fail_task
- owner: SideQuests
- mode: write
- allowed phases: solve

13. get_task_graph
- owner: SideQuests
- mode: read
- allowed phases: solve, evaluate, finalization

## Prompt Block Ownership

Prompt blocks are ARC Agent artifacts, not phase definitions.

Where blocks actually live:
- Prompt blocks are assembled by the ARC Agent prompt builder and rendered into a single prompt string at action time.
- They are exported per step under `prompt_trace.block_trace` in the submission output.
- They are not orchestration phases and are not a lifecycle state machine.

1. ObservationBlock
- producer: ARC Agent using ARC Harness observation

2. ActionFactBlock
- producer: ARC Agent HypothesisManager

3. PathHypothesisBlock
- producer: ARC Agent HypothesisManager

4. SolveContextBlock
- producer: ARC Agent SolveEngine

5. ChunkBlock
- producer: ARC Agent SolveEngine (ACTIVE CHUNK in solve context)

6. InstructionBlock
- producer: ARC Agent prompt builder

## Non-Conflict Rule

Do not map prompt blocks 1:1 to orchestration phases.

- phases answer: when in lifecycle
- blocks answer: what context payload was assembled for reasoning

## Block-by-Phase Matrix

This matrix defines expected relationships between orchestration phases and prompt blocks.

6. InstructionBlock
- producer: ARC Agent prompt builder

7. EntityContextBlock
- producer: ARC Agent using SolveEngine ObjectRoleMapper

## Non-Conflict Rule
...
| Phase | ObservationBlock | ActionFactBlock | PathHypothesisBlock | SolveContextBlock | ChunkBlock | InstructionBlock | EntityContextBlock |
|---|---:|---:|---:|---:|---:|---:|---:|
| bootstrap | - | - | - | - | - | - | O |
| hypothesize | - | O | O | - | - | - | O |
| solve | - | O | O | E | O | - | O |
| act | E | O | O | O | O | E | E |
| ingest | - | - | - | - | - | - | - |
| evaluate | - | - | - | - | - | - | - |
| finalization | - | - | - | - | - | - | - |

Notes:
- `ChunkBlock` only appears when `SolveContextBlock` has an active chunk.
- `ActionFactBlock` and `PathHypothesisBlock` depend on observed transitions and may be absent early.
- `InstructionBlock` is only required at decision time (`act`), where an action is requested.
- `ObservationBlock` in prompts is decision-facing observation context, not raw ingest payloads.
- `EntityContextBlock` appears when `_entity_map` has at least one non-UNKNOWN role.


Important clarification:
- A `-` in the matrix means "no prompt block expected for that phase," not "phase is unused."
- `bootstrap`, `ingest`, `evaluate`, and `finalization` are active lifecycle phases and are used heavily for tool calls and write traces.

Examples of non-prompt work by phase:
- `bootstrap`: API knowledge seeding, side-quest branching, initial retrieval, initial plan registration.
- `ingest`: step result ingestion (`notify_turn`) and retrieval refresh (`current_truth`).
- `evaluate`: outcome reporting (`report_outcome`) and final assessment.
- `finalization`: final narrative writes and cleanup/distillation side effects.

Rule of thumb:
- Use `prompt_trace.block_trace` to understand decision payload composition.
- Use `sidequests_ledger`, `write_trace`, and `orchestration_report` to understand lifecycle/tool activity.

## Output Contract Rule

Each run export should include:

1. prompt_trace.block_trace
- ordered block list per step with owner/tool fields

2. orchestration_report
- orchestration owner
- phase owner map
- tool rules
- runtime surfaces
- violations list
- entity_gate_status
- status

This keeps ownership explicit and makes orchestration conflicts auditable.
