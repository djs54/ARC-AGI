"""A186: fuse cross-game transferred rules (A179) into aggregate Mechanic
records, using the graph-engineering fusion pipeline (block -> match -> merge).

A179 retrieves individual rules from other games by structural fingerprint
(action_family + magnitude_class) one at a time, but never checks whether
multiple transferred rules that share a fingerprint are actually the same
underlying mechanic, and never fuses them into one reusable record. A179's
own review already found the fingerprint alone is a weak signal ("same
buttons exist" described two unrelated mechanics identically) -- fusion adds
a second, independent check (shared preconditions, see rule_extraction.py's
entity_preconditions) before trusting a fingerprint match enough to merge.

Merging is conservative by design: an incorrect merge silently combines two
unrelated mechanics' entire evidence trails, which is worse than leaving them
unfused. Ambiguous or no-match pairs are never force-merged; a rule with no
confident match to any other block member stays its own single-member,
non-confident candidate.

Pure and deterministic throughout -- no I/O, no LLM call. The LLM has no role
in this module (unlike rule_extraction.py's classify_signature, which is also
pure but feeds an LLM-naming step elsewhere): matching here is closed-form
set overlap on structured tags, exactly the kind of mechanical work the
graph-engineering fusion material says shouldn't need a model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

# Minimum number of shared precondition features for two transferred rules to
# be considered the same mechanic. Matches the graph-engineering fusion
# material's own worked example ("two 'J. Smith' nodes sharing 3 coauthors
# and an affiliation are the same person"). Tune only with evidence --
# lowering this raises the risk of an erroneous merge, which silently
# corrupts both merged rules' evidence trails.
CONFIDENT_MATCH_MIN_SHARED_PRECONDITIONS = 3


@dataclass(frozen=True, slots=True)
class TransferredRuleRecord:
    """One rule as returned by ArcGraphQueryPort.fetch_transferred_rules,
    including the fingerprint it was retrieved under and its precondition
    feature tags. `preconditions` defaults to empty because hippocampy does
    not yet store or return it (see docs/handoff/B278-mechanic-fusion.md) --
    an empty tuple can never reach the shared-feature threshold, so fusion
    degrades to "no confident match" (never a false merge) until the server
    field lands."""

    rule_id: str
    confidence: float
    source_game_id: str | None
    fingerprint: str
    preconditions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MechanicCandidate:
    """One cluster produced by match_within_block: a confident match (2+
    members whose preconditions overlap enough to fuse) or a single
    unmatched/ambiguous rule kept separate."""

    fingerprint: str
    member_rule_ids: tuple[str, ...]
    confident: bool


@dataclass(frozen=True, slots=True)
class MechanicFusionResult:
    """The deterministic merge output for one confident-match cluster -- the
    record_mechanic_fusion payload shape."""

    fingerprint: str
    member_rule_ids: tuple[str, ...]
    source_game_ids: tuple[str | None, ...]
    confidence: float
    merged_from: tuple[str, ...]


def block_by_fingerprint(rules: Sequence[TransferredRuleRecord]) -> dict[str, list[TransferredRuleRecord]]:
    """Group transferred rules by structural fingerprint (A179's blocking
    key, reused rather than reinvented)."""
    blocks: dict[str, list[TransferredRuleRecord]] = {}
    for rule in rules:
        blocks.setdefault(rule.fingerprint, []).append(rule)
    return blocks


def _shared_precondition_count(a: TransferredRuleRecord, b: TransferredRuleRecord) -> int:
    return len(set(a.preconditions) & set(b.preconditions))


def match_within_block(
    block: Sequence[TransferredRuleRecord],
    *,
    min_shared_preconditions: int = CONFIDENT_MATCH_MIN_SHARED_PRECONDITIONS,
) -> list[MechanicCandidate]:
    """Score every pair within one fingerprint block by shared precondition
    features. Pairs meeting the threshold are unioned into one cluster
    (transitively, via union-find) and reported as a confident match; every
    rule not joined to any confident match stays its own single-member,
    non-confident candidate. Never force-merges a pair below threshold --
    including a fingerprint match with disjoint or too-few shared
    preconditions."""
    if not block:
        return []

    fingerprint = block[0].fingerprint
    count = len(block)
    parent = list(range(count))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        root_i, root_j = find(i), find(j)
        if root_i != root_j:
            parent[root_i] = root_j

    for i in range(count):
        for j in range(i + 1, count):
            if _shared_precondition_count(block[i], block[j]) >= min_shared_preconditions:
                union(i, j)

    clusters: dict[int, list[int]] = {}
    for i in range(count):
        clusters.setdefault(find(i), []).append(i)

    return [
        MechanicCandidate(
            fingerprint=fingerprint,
            member_rule_ids=tuple(block[i].rule_id for i in members),
            confident=len(members) >= 2,
        )
        for members in clusters.values()
    ]


def merge_confident_candidates(
    block: Sequence[TransferredRuleRecord],
    candidates: Sequence[MechanicCandidate],
) -> list[MechanicFusionResult]:
    """Deterministic merge policy (no LLM call): for each confident-match
    candidate, produce one MechanicFusionResult. Aggregate confidence is the
    mean of member confidences, capped strictly below the strongest member's
    confidence -- multiple corroborating transferred rules are evidence, but
    never stronger evidence than the single best rule among them. Ambiguous
    (non-confident) candidates are skipped here; they stay unfused."""
    by_id = {rule.rule_id: rule for rule in block}
    results: list[MechanicFusionResult] = []
    for candidate in candidates:
        if not candidate.confident:
            continue
        members = [by_id[rule_id] for rule_id in candidate.member_rule_ids if rule_id in by_id]
        if len(members) < 2:
            continue
        confidences = [member.confidence for member in members]
        strongest = max(confidences)
        mean_confidence = sum(confidences) / len(confidences)
        aggregate_confidence = min(mean_confidence, strongest * 0.99) if strongest > 0 else 0.0
        results.append(
            MechanicFusionResult(
                fingerprint=candidate.fingerprint,
                member_rule_ids=candidate.member_rule_ids,
                source_game_ids=tuple(member.source_game_id for member in members),
                confidence=aggregate_confidence,
                merged_from=candidate.member_rule_ids,
            )
        )
    return results


def fuse_transferred_rules(
    rules: Sequence[TransferredRuleRecord],
    *,
    min_shared_preconditions: int = CONFIDENT_MATCH_MIN_SHARED_PRECONDITIONS,
) -> list[MechanicFusionResult]:
    """End-to-end block -> match -> merge over a set of transferred rules
    (typically all rules returned for one fingerprint query). Each stage
    remains independently testable; this is a convenience wrapper."""
    results: list[MechanicFusionResult] = []
    for block in block_by_fingerprint(rules).values():
        candidates = match_within_block(block, min_shared_preconditions=min_shared_preconditions)
        results.extend(merge_confident_candidates(block, candidates))
    return results


__all__ = [
    "CONFIDENT_MATCH_MIN_SHARED_PRECONDITIONS",
    "TransferredRuleRecord",
    "MechanicCandidate",
    "MechanicFusionResult",
    "block_by_fingerprint",
    "match_within_block",
    "merge_confident_candidates",
    "fuse_transferred_rules",
]
