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

from dataclasses import dataclass
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
