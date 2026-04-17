# ARC-AGI-3 Compliance Checklist

Date captured: 2026-03-28 (UTC)
Source baseline: benchmarks/arc3/rules_snapshot.md
Purpose: Pre-run and pre-submission compliance gate for ARC-AGI-3 work

## Checklist Fields

- ID: Stable check identifier
- Rule: Human-readable requirement
- Check: Machine-checkable condition
- Source: Primary source URL
- Status: pass | fail | unknown

## Checks

1. ID: ARC3-SUBMIT-KAGGLE
- Rule: Submission must be through designated Kaggle competition.
- Check: Submission pipeline target equals Kaggle competition endpoint/workflow.
- Source:
  - https://arcprize.org/competitions/2026
  - https://arcprize.org/competitions/2026/arc-agi-3
- Status: unknown

2. ID: ARC3-OPEN-SOURCE-PRIZE
- Rule: Prize eligibility requires open-sourced code/methods.
- Check: Repository/license evidence exists and is public before prize claim.
- Source:
  - https://arcprize.org/competitions/2026
  - https://www.kaggle.com/competitions/arc-prize-2026-arc-agi-3/overview
- Status: unknown

3. ID: ARC3-NETWORK-DISABLED
- Rule: Internet access disabled during evaluation.
- Check: Runtime config sets no-network mode for evaluation runs.
- Source:
  - https://arcprize.org/competitions/2026
  - https://www.kaggle.com/competitions/arc-prize-2026-arc-agi-3/overview
- Status: unknown

4. ID: ARC3-RUNTIME-CPU-LIMIT
- Rule: CPU notebook runtime <= 6h.
- Check: End-to-end CPU run completes in <= 21600 seconds.
- Source:
  - https://www.kaggle.com/competitions/arc-prize-2026-arc-agi-3/overview
- Status: unknown

5. ID: ARC3-RUNTIME-GPU-LIMIT
- Rule: GPU notebook runtime <= 6h.
- Check: End-to-end GPU run completes in <= 21600 seconds.
- Source:
  - https://www.kaggle.com/competitions/arc-prize-2026-arc-agi-3/overview
- Status: unknown

6. ID: ARC3-NOTEBOOK-ONLY
- Rule: Submission must be made via Kaggle Notebooks.
- Check: Submission artifact generated from notebook workflow only.
- Source:
  - https://www.kaggle.com/competitions/arc-prize-2026-arc-agi-3/overview
- Status: unknown

7. ID: ARC3-API-KEY
- Rule: API requests require X-API-Key.
- Check: Client sends X-API-Key header on every REST call.
- Source:
  - https://raw.githubusercontent.com/arcprize/docs/main/rest_overview.mdx
  - https://raw.githubusercontent.com/arcprize/docs/main/arc3v1.yaml
- Status: unknown

8. ID: ARC3-SESSION-AFFINITY
- Rule: Session cookies (AWSALB*) must be preserved across game session calls.
- Check: HTTP client cookie jar persists RESET/ACTION response cookies and replays them in subsequent requests for same session.
- Source:
  - https://raw.githubusercontent.com/arcprize/docs/main/rest_overview.mdx
  - https://raw.githubusercontent.com/arcprize/docs/main/arc3v1.yaml
- Status: unknown

9. ID: ARC3-ACTION-SPACE
- Rule: Agent uses official action commands only (RESET, ACTION1..ACTION7 with ACTION6 coordinate constraints).
- Check: Outbound command validator enforces endpoint/payload schema and ACTION6 x,y in [0,63].
- Source:
  - https://raw.githubusercontent.com/arcprize/docs/main/actions.mdx
  - https://raw.githubusercontent.com/arcprize/docs/main/arc3v1.yaml
- Status: unknown

10. ID: ARC3-REASONING-PAYLOAD-LIMIT
- Rule: Optional reasoning blob is capped (<= 16 KB).
- Check: Client truncates/rejects reasoning payloads exceeding 16384 bytes serialized.
- Source:
  - https://raw.githubusercontent.com/arcprize/docs/main/arc3v1.yaml
- Status: unknown

11. ID: ARC3-SCORING-CAP
- Rule: Score is capped at 100% and final score averages per-game scores.
- Check: Local evaluator sanity tests reproduce capped, averaged behavior.
- Source:
  - https://www.kaggle.com/competitions/arc-prize-2026-arc-agi-3/overview
  - https://raw.githubusercontent.com/arcprize/docs/main/scoring.md
- Status: unknown

12. ID: ARC3-HARDWARE-SPECS-PUBLISHED
- Rule: Explicit CPU/GPU/RAM specs must be sourced before hardcoding infra assumptions.
- Check: Hardware spec references exist in current official competition docs; otherwise block hardcoded assumptions.
- Source:
  - https://arcprize.org/competitions/2026/arc-agi-3
  - https://www.kaggle.com/competitions/arc-prize-2026-arc-agi-3/overview
- Status: unknown

## Suggested Preflight Result Policy

- pass: all checks except ARC3-HARDWARE-SPECS-PUBLISHED pass, and no pipeline step depends on unknown hardware values.
- fail: any mandatory rule check fails.
- unknown: missing evidence or unrun check; treat as fail for release submissions.
