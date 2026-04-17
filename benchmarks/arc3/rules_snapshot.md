# ARC-AGI-3 Rules Snapshot

Date captured: 2026-03-28 (UTC)
Scope: ARC Prize 2026 ARC-AGI-3 competition and official ARC docs
Status: Verified from primary sources listed below

## Primary Sources

- ARC Prize 2026 overview: https://arcprize.org/competitions/2026
- ARC-AGI-3 competition page: https://arcprize.org/competitions/2026/arc-agi-3
- ARC Prize testing policy: https://arcprize.org/policy
- ARC docs repo (official): https://github.com/arcprize/docs
- ARC REST overview: https://raw.githubusercontent.com/arcprize/docs/main/rest_overview.mdx
- ARC OpenAPI contract: https://raw.githubusercontent.com/arcprize/docs/main/arc3v1.yaml
- ARC actions reference: https://raw.githubusercontent.com/arcprize/docs/main/actions.mdx
- ARC scoring methodology page source: https://raw.githubusercontent.com/arcprize/docs/main/scoring.md
- Kaggle ARC-AGI-3 competition overview: https://www.kaggle.com/competitions/arc-prize-2026-arc-agi-3/overview

## Verified Rule Claims

1. Submission channel
- Rule: Submissions are through the designated Kaggle competition.
- Evidence:
  - ARC Prize general conditions: solutions must be submitted through designated Kaggle competitions.
  - ARC-AGI-3 page: submissions must be made through the designated Kaggle competition.

2. Open-source requirement for prize eligibility
- Rule: Code and methods must be open sourced for prize eligibility.
- Evidence:
  - ARC Prize 2026 rules (open-source license and eligibility language).
  - ARC-AGI-3 Kaggle page states prize-eligible solutions must be open sourced.

3. Internet access during evaluation
- Rule: Internet access is disabled during Kaggle evaluation.
- Evidence:
  - ARC Prize 2026 general conditions explicitly state no internet access during Kaggle evaluation.
  - Kaggle Code Requirements explicitly state internet access disabled.

4. Notebook runtime limits (Kaggle)
- Rule: CPU Notebook <= 6 hours runtime and GPU Notebook <= 6 hours runtime to submit.
- Evidence:
  - Kaggle ARC-AGI-3 Code Requirements section.

5. Scoring framing and cap
- Rule: Individual game score range is 0-100%; final score averages individual game scores across levels; scores cap at 100%.
- Evidence:
  - Kaggle ARC-AGI-3 Evaluation section.
  - ARC scoring methodology docs describe per-level efficiency normalization and capped score behavior.

6. Action accounting definition (benchmark methodology)
- Rule: Actions are discrete environment interactions; internal model/tool operations are not counted as actions.
- Evidence:
  - ARC scoring methodology docs (definition of an action).

7. API access/auth requirement (platform API)
- Rule: API requests require X-API-Key.
- Evidence:
  - REST overview doc and OpenAPI security scheme.

8. Stateful session affinity requirement
- Rule: Cookie affinity (AWSALB* cookies) must be preserved across RESET/ACTION calls for the same session.
- Evidence:
  - REST overview and OpenAPI descriptions for RESET/ACTION commands.

## Runtime Environment Constraints: Verified vs Unverified

Verified now:
- Kaggle runtime gating: notebook-only submission, 6h CPU/GPU run time, internet disabled.

Not explicitly published in retrieved primary sources:
- Exact CPU type/count
- Exact GPU model/count for ARC-AGI-3 evaluation runs
- Exact RAM limits
- Exact disk limits

Interpretation for engineering:
- Treat concrete hardware specs as external competition configuration, not fixed assumptions.
- Enforce only verified constraints in preflight gates until official hardware values are published.

## Submission Format and Protocol

Verified now:
- Kaggle submission file is automatically generated when an agent takes action on games.
- Leaderboard scoring follows ARC-AGI-3 methodology (0-100 capped, averaged across games/levels).

Unverified in currently fetched public sources:
- Exact row/column schema of any downloadable CSV artifact for this track.

Engineering guardrail:
- Do not hardcode a custom file schema for ARC-AGI-3 unless directly extracted from current Kaggle Rules/Data pages or official examples.

## Notes

- This snapshot intentionally distinguishes hard requirements from unknowns.
- Re-validate before milestone deadlines because ARC pages indicate rules/accelerators may change during the competition window.
