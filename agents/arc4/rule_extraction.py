"""A177: deterministic causal-rule-signature extraction from observed transitions.

Per-action tally counters (falsified_count, evidence_count) can only answer
"has this worked before" -- a bandit-arm question. A rule ("action=ACTION6
turns color 2 cells into color 5") is a falsifiable claim that can transfer
to a new game and either hold or not, which is what the architecture's
mission statement actually asks the graph to decide.

Division of labor: this module is the deterministic half -- extracting
candidate rule signatures from A176's already-computed color-transition
histogram is pattern-matching over structured data, not free-form reasoning,
so it doesn't need an LLM. The LLM's job (not implemented here) shrinks to
naming/generalizing/choosing among these structured candidates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Mapping, Sequence


def action_family(action_id: str) -> str:
    """Strip a click-target book_id suffix (e.g. "ACTION6@12,34") down to the
    base action family ("ACTION6"), mirroring evaluator.py::_action_family."""
    for separator in ("@", ":", "-", "_"):
        if separator in action_id:
            return action_id.split(separator, 1)[0]
    return action_id


@dataclass(frozen=True, slots=True)
class RuleSignature:
    """A candidate causal claim: this action family tends to turn cells of
    `from_color` into `to_color`. Deliberately coarse (no spatial/radius
    component yet) -- start simple, extend only when evidence shows this
    granularity is insufficient, matching A175's Step 0 philosophy."""

    action_family: str
    from_color: Any
    to_color: Any

    def key(self) -> str:
        return f"{self.action_family}:{self.from_color}->{self.to_color}"


def extract_candidate_signatures(
    action_id: str,
    color_transitions: Sequence[Mapping[str, Any]],
) -> list[RuleSignature]:
    """Extract one candidate rule signature per distinct (from, to) color pair
    in an action's observed color-transition histogram (A176's already-
    computed summary -- no new grid pass needed)."""
    family = action_family(action_id)
    return [
        RuleSignature(action_family=family, from_color=ct.get("from"), to_color=ct.get("to"))
        for ct in color_transitions
        if "from" in ct and "to" in ct
    ]


@dataclass(frozen=True, slots=True)
class ExistingRule:
    """A minimal view of a rule already known to the graph, as returned by
    `ArcGraphQueryPort.fetch_rules_for_action`."""

    rule_id: str
    action_family: str
    from_color: Any
    to_color: Any
    confidence: float = 0.0
    falsified: bool = False


ClassificationResult = Literal["confirms", "falsifies", "new"]


def classify_signature(signature: RuleSignature, existing_rules: Sequence[ExistingRule]) -> dict[str, Any]:
    """Given a newly-observed signature and the set of rules already known
    for this action_family, determine whether it confirms an existing rule
    (same from AND to), falsifies one (same action_family + from_color, but a
    *different* to_color -- the rule predicted one outcome, a different one
    happened), or represents an entirely new claim (no existing rule covers
    this action_family + from_color at all)."""
    confirmed: list[str] = []
    falsified: list[str] = []
    for rule in existing_rules:
        if rule.action_family != signature.action_family or rule.from_color != signature.from_color:
            continue
        if rule.to_color == signature.to_color:
            confirmed.append(rule.rule_id)
        else:
            falsified.append(rule.rule_id)

    if confirmed:
        result: ClassificationResult = "confirms"
    elif falsified:
        result = "falsifies"
    else:
        result = "new"

    return {
        "signature": signature,
        "result": result,
        "confirmed_rule_ids": confirmed,
        "falsified_rule_ids": falsified,
    }


# ── A179: structural fingerprint for cross-game transfer ──────────────────
#
# action_family (ACTION1..ACTION7) is already game-invariant -- fixed by the
# ARC-AGI-3 action vocabulary, not a per-game palette choice. Literal colors
# are exactly the opposite: "color 3" means nothing across two games with
# different palettes. So the fingerprint keeps action_family and replaces
# colors with a magnitude bucket (how many cells a transition touched) --
# color-invariant, but still captures real shape difference ("this action
# toggles one cell" vs "this action recolors a whole region" are genuinely
# different mechanics, and that distinction *does* transfer across palettes).

_MAGNITUDE_SMALL_MAX = 4
_MAGNITUDE_LARGE_MIN = 20


def magnitude_class(changed_count: int) -> str:
    if changed_count <= 1:
        return "single"
    if changed_count <= _MAGNITUDE_SMALL_MAX:
        return "small"
    if changed_count < _MAGNITUDE_LARGE_MIN:
        return "medium"
    return "large"


@dataclass(frozen=True, slots=True)
class StructuralFingerprint:
    action_family: str
    magnitude: str

    def key(self) -> str:
        return f"{self.action_family}:{self.magnitude}"


def compute_fingerprint(action_id: str, changed_count: int) -> StructuralFingerprint:
    return StructuralFingerprint(action_family=action_family(action_id), magnitude=magnitude_class(changed_count))


# ── A186: precondition features for cross-game mechanic fusion ────────────
#
# A179's fingerprint says two transferred rules *might* be the same mechanic
# ("same buttons exist" was already shown to be a weak signal on its own --
# see A179's own review). Fusion needs a second, independent check before
# trusting that enough to merge two rules' evidence: do they actually share
# the state they fired on? These features describe the entity a rule fired
# on, deliberately excluding literal color for the same reason the
# fingerprint does -- "color 3" means nothing across two games with
# different palettes, but "small, roughly square blob" does.


def shape_class(height: int, width: int) -> str:
    """Palette- and scale-invariant bounding-box shape bucket (aspect ratio
    only)."""
    if height <= 0 or width <= 0:
        return "degenerate"
    ratio = height / width
    if ratio > 1.25:
        return "tall"
    if ratio < 0.8:
        return "wide"
    return "square"


def entity_preconditions(kind: str, cell_count: int | None, bbox: Sequence[int] | None) -> list[str]:
    """Deterministic, palette-invariant feature tags describing an entity
    just before a rule fired on it -- the evidence mechanic_fusion.py's
    structure-layer matching compares between transferred rules."""
    features = [f"kind:{kind}"]
    if cell_count is not None:
        features.append(f"size_class:{magnitude_class(int(cell_count))}")
    if bbox is not None and len(bbox) == 4:
        min_row, min_col, max_row, max_col = bbox
        features.append(f"shape_class:{shape_class(max_row - min_row + 1, max_col - min_col + 1)}")
    return features


# ── A219: entity-level effect-type classification (perception layer, v1) ──
#
# `perceive.py::_assign_correspondence` (A175) already matches entities
# frame-to-frame via nearest-centroid, same-color matching, assigning a
# stable `entity_ref` -- but only ever used that for ID assignment, never
# to classify *what* changed. This turns that already-computed before/after
# pairing into a real (if coarse) effect-type classification, entity by
# entity, where today the only classification anywhere in this codebase is
# evaluator.py's crude 4-bucket `observed_kind` (no_change/grid_change/
# level_gain/state_change) -- `grid_change` is a catch-all that can't tell
# "one pixel flipped" from "an object translated across the board." See
# backlog/A216.md Part 2 for the full taxonomy discussion and
# backlog/A219.md for this v1 slice's explicit scope.
#
# v1 deliberately omits RECOLOR, MERGE, and SPLIT: RECOLOR would need
# relaxing `_assign_correspondence`'s color-locked matching (a load-bearing
# function everything depending on entity_ref stability relies on -- too
# risky to bundle here), and MERGE/SPLIT need many-to-one/one-to-many
# matching, a structurally bigger change to the same function. GLOBAL is
# left to `magnitude_class` above, which already covers it. All four are
# explicitly deferred to a future card (backlog/A219.md's Scope note).


class EffectType(StrEnum):
    TRANSLATION = "translation"
    GROWTH = "growth"
    SHRINK = "shrink"
    APPEARANCE = "appearance"
    DISAPPEARANCE = "disappearance"
    UNCHANGED = "unchanged"


def classify_effect_type(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any] | None,
    *,
    translation_threshold: float = 2.0,
    size_delta_threshold: int = 1,
) -> EffectType:
    """Pure, deterministic entity-level effect classification (Shift-A
    compliant, no LLM) from a matched entity's previous/current attribute
    dicts (a `PerceivedEntity.attributes` mapping -- expects `centroid`
    and/or `cell_count` keys when present, both optional so this degrades
    gracefully if either is missing).

    `previous`/`current` follow `_assign_correspondence`'s own correspondence:
    `previous is None` means this `entity_ref` has no predecessor (a fresh
    appearance this frame); `current is None` means a previous-frame entity
    went unclaimed this frame (a disappearance -- see
    `perceive.py::PerceiveAgent._find_disappeared_entities`, new in A219,
    since nothing computed this before). When both are present, they're
    assumed to be the same `entity_ref`.

    `translation_threshold`/`size_delta_threshold` are unvalidated starting
    points -- no empirical basis yet, matching this repo's established
    "no empirical basis yet, tune with real data" convention (A217's
    `AnnatarLimits.complex_domain_deepening_multiplier` is the most recent
    precedent). Translation is checked before growth/shrink when an entity
    both moved and resized in the same step -- an arbitrary but documented
    tie-break, not derived from evidence.
    """
    if previous is None and current is None:
        # Degenerate input -- neither an appearance, disappearance, nor a
        # real correspondence. Not expected from `_assign_correspondence`'s
        # own call sites, but treated as a no-op rather than raising.
        return EffectType.UNCHANGED
    if previous is None:
        return EffectType.APPEARANCE
    if current is None:
        return EffectType.DISAPPEARANCE

    prev_centroid = previous.get("centroid")
    cur_centroid = current.get("centroid")
    if prev_centroid is not None and cur_centroid is not None:
        distance = math.dist(prev_centroid, cur_centroid)
        if distance > translation_threshold:
            return EffectType.TRANSLATION

    prev_cell_count = previous.get("cell_count")
    cur_cell_count = current.get("cell_count")
    if prev_cell_count is not None and cur_cell_count is not None:
        delta = cur_cell_count - prev_cell_count
        if delta > size_delta_threshold:
            return EffectType.GROWTH
        if delta < -size_delta_threshold:
            return EffectType.SHRINK

    return EffectType.UNCHANGED
