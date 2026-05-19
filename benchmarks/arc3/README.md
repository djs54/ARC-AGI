# ARC-AGI-3 SideQuests Submission Artifact

This directory contains the final submission runner and compliance validation tools for the SideQuests memory-augmented agent in the ARC-AGI-3 contest.

## Contents

- `submission.py`: Main entry point for contest evaluators. Runs the memory-augmented agent on all puzzles.
- `pre_submit_check.py`: Automated compliance tool to verify offline status, model budgets, and output formats.
- `PROMPT_STRATEGY.md`: ARC-SPECIFIC prompt equation, limits, and compression rules.
- `model_budget.yaml`: Resource constraints and model configuration.
- `offline_manifest.json`: Manifest for the offline submission bundle.
- `tasks_manifest.json`: Puzzle set to be solved.

## Quick Start (For Evaluators)

To run the full evaluation:

```bash
# 1. Initialize environment (if needed)
# pip install -r requirements.txt

# 2. Run the evaluation
python benchmarks/arc3/submission.py
```

Results will be exported to `submission_results.json` in the current directory.

## Compliance Verification

Before submitting, run the pre-submission check:

```bash
python benchmarks/arc3/pre_submit_check.py
```

## Architecture

SideQuests uses a "Gated Consolidation Loop" to maintain persistent memory across puzzles.
1. **Observation**: Each ARC step is normalized into a semantic narrative.
2. **Ingestion**: The narrative is stored in an embedded graph database (Kùzu).
3. **Consolidation**: A background process extracts "Concepts" and "Decisions" from the narrative.
4. **Retrieval**: Before choosing an action, the agent queries the brain for similar historical patterns.
5. **Action**: The agent makes an informed choice based on its current observation and recalled memory.

## API Contract Ingestion Caching (B108)

To reduce redundant SideQuests overhead, the harness caches stable ARC protocol concepts.
- **Precomputed Knowledge**: The `ARC-AGI-3 API Contract` is ingested using precomputed gist/schema classifications.
- **Fast-Track Ingestion**: This bypasses expensive LLM calls during the consolidation loop, ensuring that the agent stop paying full ingestion cost for the same contract knowledge every time.
- **Efficiency**: The optimization is bounded to stable protocol concepts to ensure puzzle-specific cognition is not suppressed.

## Meta-Harness & Experience Store (B104)

The **Meta-Harness** is the outer loop that evolves the **ARC Harness**. To do this, it leverages a persistent graph-native **Experience Store** in SideQuests.

The Experience Store tracks:
- `HarnessCandidate`: A specific version of the ARC harness code and prompt logic.
- `HarnessEvalRun`: The results of running a candidate against a puzzle set.
- `HarnessMutation`: An atomic change to a harness (e.g., "prompt slimming", "retrieval gating").
- `HarnessScoreSummary`: Aggregated performance (success rate, tokens, runtime).
- `HarnessFailureCluster`: Common failure modes (e.g., "no-progress loop on puzzles with size 10").
- `PuzzleTraceRef`: Pointer to the full step-by-step history of a puzzle solve attempt.

This split allows SideQuests to support harness evolution. The meta-harness can ask questions about its own history (not just puzzle history) to find better harness candidates.

## Meta-Harness Query Surface (B105)

To support automated harness evolution, `model_eval.py` provides a **MetaHarnessQuerySurface** for navigating prior results without filesystem scraping.

### Proposer-Facing Queries

- **list_top_candidates(summaries, metric, limit)**: Ranks harness versions by solve rate, tokens, or latency.
- **compare_candidates(baseline, candidate)**: Calculates deltas for key performance metrics between two versions.
- **list_failure_clusters(results)**: Groups failed tasks by their final state or signature.
- **list_regressions(baseline_results, candidate_results)**: Identifies puzzles that solved in a prior version but fail now.

### Candidate Runner (B106)

The **MetaHarnessRunner** provides the first executable outer-loop evaluation path.

- **HarnessCandidate**: Represents a specific version/configuration of the ARC harness.
- **HarnessEvalRun**: Captures the result of evaluating a candidate, including solve rate, budget, lineage, and failure summaries.
- **Evaluation Contract**: The runner evaluates candidates against a fixed puzzle set, persisting result bundles for comparison.

### Proposer Loop (B107)

The **Meta-Harness Proposer Loop** is the automated engine that iterates on harness versions.

1.  **Retrieve**: Queries the experience store for prior results and failures.
2.  **Propose**: Suggests a mutation (threshold change, prompt tweak, or trigger shift).
3.  **Evaluate**: Runs the candidate through the MetaHarnessRunner.
4.  **Select**: Compares metrics against the baseline to find the new "best" configuration.

The search policy is bounded to prompt logic, heuristic thresholds, and retrieval parameters to ensure stable, directed evolution.

Coding agents should use these helpers to evaluate mutations and decide whichMutations to propose next.

## Prompt Budget & Retrieval Budget Benchmark (B89)

The submission collects repeatable metrics to measure prompt strategy effectiveness:

### Metrics Collected

**Prompt Budget:**
- `avg_tokens_per_step`: Mean token count across all decision prompts
- `max_tokens_per_step` / `min_tokens_per_step`: Token extremes
- `first_prompt_detail_level`: "rich" (includes memory/facts) or "compact" (minimal context)
- `asked_for_decision_from_effects`: Whether prompt explicitly asks for decision based on observed effects
- `invalid_action_count`: Actions outside available set before policy enforcement
- `no_progress_step_count`: Steps with zero reward (exploration cost)

**Retrieval Budget:**
- `retrieval_count`: Number of memory queries (perceive phase)
- `avg_retrieval_size_bytes`: Mean byte size of retrieved context
- `total_retrieval_size_bytes`: Cumulative retrieval payload

The exported submission rows keep these metrics under `metadata.benchmark_metrics`, and
[`benchmarks/arc3/model_eval.py`](/Users/djshelton/Desktop/GitProjects/ARC_AGI/benchmarks/arc3/model_eval.py)
now includes helpers to compare two result rows and report prompt-budget deltas.

### Comparison Baseline

Use **puzzle-1** as the fixed comparison target for all strategy tuning. When evaluating changes:
1. Record baseline metrics on puzzle-1 under current strategy
2. Apply prompt compression, first-input changes, or retrieval budget modifications
3. Compare new puzzle-1 results against baseline on:
   - Did `avg_tokens_per_step` decrease while `correct` stayed true?
   - Did richer first-input improve retrieval usefulness (check memory usage in step_history)?
   - Did retrieval budget tradeoffs affect action quality (fewer/more invalid_actions)?

The preferred comparison is:
- `compact` first prompt versus `rich` first prompt
- the same puzzle-1 task under otherwise identical settings
- compare the exported rows with `build_arc_prompt_budget_comparison_report(...)`

### Prompt Budget Targets

- `avg_tokens_per_step`: Target 150–250 (varies by detail_level)
- `first_prompt_detail_level`: rich for exploration, compact for exploitation
- `asked_for_decision_from_effects`: true (grounds decisions in observations)
- `invalid_action_count`: ≤ 2 per puzzle (policy enforcement catches most)
- `no_progress_step_count`: < 40% of total steps
- `runtime_seconds`: should trend down as prompt shape improves

### Retrieval Budget Targets

- `avg_retrieval_size_bytes`: < 2000 bytes (compact, focused recalls)
- `total_retrieval_size_bytes`: < 15000 bytes per puzzle (efficient memory use)
- keep retrieval triggered only when it can change the next move
